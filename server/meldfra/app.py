"""MeldFra — monteres inn i en eksisterende FastAPI-app.

    from meldfra import MeldFra, Settings

    meldfra = MeldFra(Settings.from_env(), get_user=current_user_id, get_admin=require_admin)
    app.include_router(meldfra.router, prefix="/meldfra")
    # i lifespan:  await meldfra.start()  …  await meldfra.stop()

`get_user` og `get_admin` er vanlige FastAPI-avhengigheter fra vertsløsningen. Modulen har
ingen egen innlogging — den stoler på at verten vet hvem brukeren er.
"""
import asyncio
import base64
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from fastapi.responses import FileResponse

from . import interview, sync
from .config import Settings
from .embeddings import Embedder
from .llm import AnthropicLLM
from .scrub import Scrubber
from .store import Store
from .trackers import from_settings
from .triage import Triage

MAX_TEXT = 4000
MAX_TITLE = 120
MAX_STEPS = 10
MAX_ANSWER = 500
MAX_SCREENSHOT_BYTES = 3 * 1024 * 1024
DEVICE_KEYS = ("browser", "os", "viewport", "pixel_ratio", "locale", "app_version", "timezone")
STATIC = Path(__file__).parent / "static"


def _user_id(u):
    """Godtar en streng/int, en dict med 'id', eller et objekt med .id."""
    if u is None or u is False:
        return None
    if isinstance(u, (str, int)):
        return str(u)
    if isinstance(u, dict):
        return str(u["id"]) if u.get("id") is not None else None
    v = getattr(u, "id", None)
    return str(v) if v is not None else None


class _RateLimiter:
    """Enkel glidende-vindu-begrensning i minnet, per bruker og rute. Holder for én prosess;
    kjører verten flere instanser, bør den heller begrense i proxyen."""

    def __init__(self):
        self.hits = defaultdict(deque)

    def check(self, key, per_min):
        if not per_min:
            return
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_min:
            raise HTTPException(429, "For mange forespørsler — vent litt og prøv igjen.")
        q.append(now)


def _clean_crumbs(raw, limit=50):
    """Klienten logger allerede bare etiketter, men vi stoler ikke på det: kun kjente felt,
    korte strenger, primitive data-verdier."""
    if not isinstance(raw, list):
        return None
    out = []
    for c in raw[-limit:]:
        if not isinstance(c, dict):
            continue
        data = c.get("data") if isinstance(c.get("data"), dict) else None
        if data:
            data = {str(k)[:30]: v for k, v in list(data.items())[:8] if isinstance(v, (str, int, float, bool))}
            data = {k: (v[:120] if isinstance(v, str) else v) for k, v in data.items()}
        item = {
            "t": c.get("t") if isinstance(c.get("t"), (int, float)) else None,
            "type": str(c.get("type") or "")[:20],
            "category": str(c.get("category") or "")[:40],
            "message": str(c.get("message") or "")[:120],
            "level": c.get("level") if c.get("level") in ("info", "warning", "error") else "info",
        }
        if data:
            item["data"] = data
        out.append(item)
    return out or None


def _parse_body(body):
    if not isinstance(body, dict):
        raise HTTPException(400, "Ugyldig innsending")
    return body


class MeldFra:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        get_user: Callable,
        get_admin: Optional[Callable] = None,
        notify: Optional[Callable] = None,
        llm: Any = None,
        tracker: Any = None,
        embedder: Any = None,
        store: Optional[Store] = None,
        scrubber: Optional[Scrubber] = None,
    ):
        """
        get_user:  FastAPI-avhengighet → bruker-id (str/int), dict med 'id', objekt med .id,
                   eller None (gir 401).
        get_admin: FastAPI-avhengighet som returnerer truthy for administratorer (eller kaster
                   selv). Utelatt = ingen admin-ruter monteres.
        notify:    (user_id, tittel, tekst, rapport) -> None | awaitable. Kalles når en sak
                   lukkes. Utelatt = brukeren ser statusen i widgeten («Mine rapporter»).
        """
        self.s = settings or Settings.from_env()
        self.store = store or Store(self.s.database_url)
        self.tracker = tracker or from_settings(self.s)
        if llm is None:
            a = AnthropicLLM(self.s.anthropic_api_key, self.s.llm_model, self.s.llm_effort)
            llm = a if a.available else None
        self.llm = llm
        self.embedder = embedder or Embedder(self.s.embeddings_url, self.s.embeddings_model)
        self.scrub = scrubber or Scrubber()
        self.notify = notify
        self.triage = Triage(self.store, self.tracker, self.llm, self.embedder, self.s)
        self._limiter = _RateLimiter()
        self._loop_task = None
        self._get_user = get_user
        self._get_admin = get_admin
        self.router = self._build_router()

    # ── Livssyklus ─────────────────────────────────────────────────────────────────────────
    async def start(self, create_tables=True, run_sync=True):
        if create_tables:
            await asyncio.to_thread(self.store.create_tables)
        if run_sync and self.tracker.configured and not self._loop_task:
            self._loop_task = asyncio.create_task(
                sync.run_loop(self.store, self.tracker, self.notify, self.s.poll_interval_s)
            )

    async def stop(self):
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        await self.triage.drain()

    async def sync_now(self):
        """Kjør én oppfølgingsrunde nå (f.eks. fra en cron-jobb i stedet for løkka)."""
        return await sync.sync_once(self.store, self.tracker, self.notify)

    # ── Ruter ──────────────────────────────────────────────────────────────────────────────
    def _build_router(self):
        r = APIRouter(tags=["meldfra"])
        get_user = self._get_user

        def require_user(u=Depends(get_user)):
            uid = _user_id(u)
            if not uid:
                raise HTTPException(401, "Du må være innlogget for å melde fra.")
            return uid

        @r.get("/meldfra.js", include_in_schema=False)
        async def widget():
            # Serveres herfra så verten bare trenger én <script>-tagg — ingen CDN, CSP-vennlig.
            return FileResponse(STATIC / "meldfra.js", media_type="text/javascript",
                                headers={"Cache-Control": "public, max-age=300"})

        @r.post("/interview")
        async def interview_turn(body: dict = Body(...), uid: str = Depends(require_user)):
            """Én stateless tur i KI-intervjuet. Feiler KI-en, kommer brukerens egne ord tilbake
            som utkast — en rapport skal aldri gå tapt fordi modellen var nede."""
            self._limiter.check(f"i:{uid}", self.s.rate_limit_interview_per_min)
            body = _parse_body(body)
            observed = str(body.get("observed") or "").strip()[:MAX_TEXT]
            if not observed:
                raise HTTPException(400, "Skriv en kort beskrivelse først.")
            kind_hint = body.get("kind") if body.get("kind") in ("bug", "idea") else None
            raw = body.get("answers") if isinstance(body.get("answers"), dict) else {}
            answers = {}
            if raw.get("expected"):
                answers["expected"] = str(raw["expected"])[:MAX_ANSWER]
            if isinstance(raw.get("blocking"), bool):
                answers["blocking"] = raw["blocking"]
            if raw.get("frequency") in interview.FREQUENCIES:
                answers["frequency"] = raw["frequency"]
            asked = body.get("asked")
            asked = [str(a)[:24] for a in asked[:5]] if isinstance(asked, list) else []
            crumbs = _clean_crumbs(body.get("breadcrumbs")) or []
            route = str(body.get("route") or "")[:200] or None
            try:
                return await interview.interview_turn(
                    self.llm, self.s, observed, answers, asked,
                    kind_hint=kind_hint, breadcrumbs=crumbs, route=route,
                )
            except Exception as e:
                print("meldfra: intervju feilet (faller tilbake til rå tekst):", e)
                return {"draft": {
                    "kind": kind_hint or "bug", "title": observed.split("\n")[0][:MAX_TITLE],
                    "observed": observed, "expected": answers.get("expected"), "steps": None,
                    "blocking": answers.get("blocking"), "frequency": answers.get("frequency"),
                }}

        @r.post("/reports")
        async def submit(body: dict = Body(...), uid: str = Depends(require_user)):
            """Lagre en godkjent rapport. Fritekst saneres FØR lagring; vedlegg lagres KUN ved
            eksplisitt samtykke til nettopp det vedlegget, og kun i vår egen database med TTL.
            Triagen (og tracker-saken) kjører i bakgrunnen — brukeren får ✓ med en gang."""
            self._limiter.check(f"s:{uid}", self.s.rate_limit_submit_per_min)
            body = _parse_body(body)
            kind = body.get("kind") if body.get("kind") in ("bug", "idea") else "bug"
            observed = str(body.get("observed") or "").strip()[:MAX_TEXT]
            if not observed:
                raise HTTPException(400, "Skriv en kort beskrivelse først.")
            title = (str(body.get("title") or "").strip() or observed.split("\n")[0])[:MAX_TITLE]
            expected = str(body.get("expected") or "").strip()[:MAX_TEXT] or None
            steps = body.get("steps")
            steps = [str(s)[:200] for s in steps[:MAX_STEPS] if str(s).strip()] if isinstance(steps, list) else None
            frequency = body.get("frequency") if body.get("frequency") in interview.FREQUENCIES else None
            blocking = body.get("blocking") if isinstance(body.get("blocking"), bool) else None

            raw_dev = body.get("device") if isinstance(body.get("device"), dict) else {}
            # `is not None`, ikke truthiness: False/0 kan være meningsfulle verdier.
            device = {k: str(raw_dev[k])[:120] for k in DEVICE_KEYS if raw_dev.get(k) is not None}
            if self.s.server_version:
                # Fylles av SERVEREN — klienten kan ikke lyve om den, og det avslører
                # «backend er ikke deployet ennå» uten spørsmålsrunder.
                device["server_version"] = self.s.server_version[:40]

            consent = body.get("consent") if isinstance(body.get("consent"), dict) else {}
            c_shot = consent.get("screenshot") is True
            c_crumbs = consent.get("breadcrumbs") is not False
            # Sporet lagres KUN når bryteren sto på — selv om klienten sendte det likevel.
            crumbs = _clean_crumbs(body.get("breadcrumbs")) if c_crumbs else None

            sc = self.scrub.text
            row = {
                "kind": kind,
                "title": sc(title)[:MAX_TITLE],
                "observed": sc(observed),
                "expected": sc(expected) if expected else None,
                "steps": [sc(s) for s in steps] if steps else None,
                "frequency": frequency,
                "blocking": blocking,
                "route": str(body.get("route") or "")[:200] or None,
                "app_version": device.get("app_version"),
                "device": device or None,
                "breadcrumbs": crumbs,
                "consent_screenshot": c_shot,
                "consent_breadcrumbs": c_crumbs,
            }
            shot = None
            if c_shot and isinstance(body.get("screenshot"), str) and body["screenshot"]:
                s = body["screenshot"]
                try:
                    shot = base64.b64decode(s.split(",", 1)[1] if s.startswith("data:") else s, validate=True)
                except Exception:
                    shot = None
                if shot and (len(shot) > MAX_SCREENSHOT_BYTES or not shot.startswith(b"\x89PNG")):
                    shot = None
            row["consent_screenshot"] = bool(c_shot and shot)

            rid = await asyncio.to_thread(self.store.insert_report, uid, row)
            if shot:
                try:
                    await asyncio.to_thread(
                        self.store.insert_attachment, rid, "screenshot", shot, "image/png", self.s.attachment_ttl_days,
                    )
                except Exception as e:
                    print("meldfra: skjermbilde-vedlegg feilet (rapporten beholdes):", e)
            self.triage.schedule(rid)
            return {"ok": True, "id": rid}

        @r.get("/reports")
        async def mine(uid: str = Depends(require_user)):
            """«Mine rapporter»: brukeren skal se at rapporten førte til noe."""
            return {"reports": await asyncio.to_thread(self.store.list_for_user, uid)}

        @r.post("/reports/{report_id}/verify")
        async def verify(report_id: int, body: dict = Body(...), uid: str = Depends(require_user)):
            """Brukeren kvitterer ut fiksen — eller melder «fortsatt feil», som gjenåpner saken
            (i bakgrunnen: brukeren skal ikke vente på trackeren)."""
            fixed = _parse_body(body).get("fixed")
            if not isinstance(fixed, bool):
                raise HTTPException(400, "Mangler fixed (true/false)")
            changed, tracker_name, number = await asyncio.to_thread(self.store.verify, report_id, uid, fixed)
            if not changed:
                raise HTTPException(404, "Fant ingen fikset rapport å bekrefte")
            if not fixed and number and tracker_name == self.tracker.name:
                async def _reopen():
                    await self.tracker.reopen(number)
                    await self.tracker.comment(
                        number, f"🔁 **Rapportøren melder: fortsatt feil** (rapport #{report_id}). Gjenåpnet.")
                self.triage.schedule(_reopen())
            return {"ok": True, "status": "bekreftet" if fixed else "gjenapnet"}

        @r.delete("/reports/{report_id}")
        async def remove(report_id: int, uid: str = Depends(require_user)):
            """Trekk samtykket: rapport og vedlegg slettes (art. 7(3) — like lett å trekke som å gi).
            Tracker-saken blir stående: den er sanert og eies av teamet."""
            ok = await asyncio.to_thread(self.store.delete_report, report_id, uid)
            if not ok:
                raise HTTPException(404, "Fant ikke rapporten")
            return {"ok": True}

        if self._get_admin is not None:
            get_admin = self._get_admin

            def require_admin(a=Depends(get_admin)):
                if not a:
                    raise HTTPException(403, "Kun for administratorer")
                return a

            @r.get("/admin/reports")
            async def admin_list(status: Optional[str] = None, limit: int = 100, _=Depends(require_admin)):
                return {"reports": await asyncio.to_thread(self.store.admin_list, status, min(limit, 500))}

            @r.get("/admin/reports/{report_id}/screenshot")
            async def admin_shot(report_id: int, _=Depends(require_admin)):
                data, mime = await asyncio.to_thread(self.store.get_attachment, report_id, "screenshot")
                if not data:
                    raise HTTPException(404, "Ingen skjermbilde (aldri sendt, eller utløpt)")
                return Response(data, media_type=mime or "image/png", headers={"Cache-Control": "no-store"})

            @r.post("/admin/reports/{report_id}/retriage")
            async def admin_retriage(report_id: int, _=Depends(require_admin)):
                """Kjør triagen på nytt (f.eks. når trackeren var nede ved innsending)."""
                await self.triage.run(report_id)
                return {"ok": True}

        return r
