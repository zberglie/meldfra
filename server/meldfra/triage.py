"""KI-triage av innkommende rapporter. KJØRER I BAKGRUNNEN — brukeren ser ✓ med en gang.

TOSTEGS DUPLIKATDETEKSJON:
  1. VEKTOR-RECALL (billig, bred): cosine-likhet ≥ 0.65. Bevisst LAV terskel — den skal fange
     parafraser («betalingen gikk ikke gjennom» / «fikk ikke betalt kontingenten»).
  2. KI-DOMMER (presis): 0–100 konfidens. Ren embedding-likhet er svak alene — uten dommeren
     ville vi slått sammen ulike feil på samme side.

  ≥ 90   samme feil: knytt til klyngen, kommentar på eksisterende sak, INGEN ny sak
  50–89  mulig duplikat: egen sak merket «mulig-duplikat» + kryss-lenke, mennesket avgjør
  < 50   egen sak

ALDRI AUTO-LUKK EN BRUKERS RAPPORT. Duplikater ARVER klyngens sak — dermed får ALLE som meldte
feilen beskjed når den lukkes. Invariant: én tracker-sak per klynge.

RICE-LITE: Reach (distinkte brukere i klyngen) er det eneste som kan automatiseres. Effort
settes til 1; mennesket justerer i trackeren. KI-en forbedrer INPUTENE, mennesket beholder
beslutningen.
"""
import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from .embeddings import build_text

DUPE_CERTAIN = 90
DUPE_MAYBE = 50
SEVERITIES = ("S1", "S2", "S3", "S4")
_IMPACT = {"S1": 3.0, "S2": 2.0, "S3": 1.0, "S4": 0.5}


class _Classification(BaseModel):
    severity: Literal["S1", "S2", "S3", "S4"]
    area: str
    reproducible: bool


class _DupeVerdict(BaseModel):
    confidence: int = Field(description="0-100")
    reason: str


def classify_system(product_name, product_description, areas):
    return (
        f"Du triagerer en feilrapport for {product_name} ({product_description}).\n\n"
        "SEVERITY:\n"
        "  S1 = data går tapt, betaling/innmelding feiler, eller brukeren kommer ikke videre\n"
        "  S2 = sentral funksjon virker ikke, men det finnes en omvei\n"
        "  S3 = irriterende feil som ikke stopper brukeren\n"
        "  S4 = kosmetisk / tekst / småtterier\n"
        f"area: nøyaktig én av {', '.join(areas)}. Bruk 'annet' når ingen passer.\n"
        "reproducible: true hvis rapporten har konkrete steg eller en teknisk feil (f.eks. "
        "HTTP 500) som lar en utvikler gjenskape den."
    )


DUPE_SYSTEM = (
    "Du avgjør om to feilrapporter beskriver SAMME underliggende feil.\n"
    "100 = utvilsomt samme feil. 0 = helt ulike feil.\n"
    "Samme SIDE er IKKE nok — to ulike feil på samme side er ikke duplikater.\n"
    "Ulik ordlyd om samme symptom ER duplikat.\n"
    "Vær KONSERVATIV: er du i tvil, svar under 50. En feilaktig sammenslåing skjuler en ekte feil."
)


def rice_score(reach, severity, reproducible, blocking, frequency, effort=1.0):
    """Confidence: 100 % når det er reproduserbart OG alvorlig/hyppig, 80 % når reproduserbart
    eller meldt av flere, ellers 50 %."""
    impact = _IMPACT.get(severity, 1.0)
    if reproducible and (blocking or frequency == "hver-gang"):
        confidence = 1.0
    elif reproducible or reach > 1:
        confidence = 0.8
    else:
        confidence = 0.5
    try:
        eff = float(effort) or 1.0
    except (TypeError, ValueError):
        eff = 1.0
    return round((max(1, reach) * impact * confidence) / eff, 2)


def labels(kind, severity, area, maybe_dupe=False):
    """Flate labels: virker likt på GitHub og GitLab Free (scoped labels og weight er Premium)."""
    prio = {"S1": "prio/P1", "S2": "prio/P2", "S3": "prio/P3", "S4": "prio/P4"}.get(severity, "prio/P3")
    out = ["type/idé" if kind == "idea" else "type/feil", "meldfra", f"sev/{severity or 'S3'}", prio,
           f"omr/{area or 'annet'}"]
    if maybe_dupe:
        out.append("mulig-duplikat")
    return out


def issue_body(report_id, row, severity, area, score, reach, admin_base_url=""):
    """Saksteksten. Alt er sanert før det kommer hit. Vedlegg REFERERES, lastes aldri opp."""
    parts = [row.get("observed") or ""]
    if row.get("expected"):
        parts.append(f"**Forventet:**\n{row['expected']}")
    if row.get("steps"):
        parts.append("**Steg:**\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(row["steps"], 1)))

    meta = [
        f"- rapport: #{report_id}",
        f"- alvorlighet: **{severity}** · område: **{area}** · **RICE {score}** (reach {reach})",
    ]
    if row.get("route"):
        meta.append(f"- side: `{row['route']}`")
    if row.get("frequency"):
        meta.append(f"- frekvens: {row['frequency']}")
    if row.get("blocking") is not None:
        meta.append(f"- blokkerende: {'ja' if row['blocking'] else 'nei'}")
    for k in ("browser", "os", "viewport", "locale", "app_version", "server_version"):
        v = (row.get("device") or {}).get(k)
        if v:
            meta.append(f"- {k}: {v}")
    parts.append("**Kontekst:**\n" + "\n".join(meta))

    crumbs = row.get("breadcrumbs") or []
    if crumbs:
        lines = [
            f"- `{c.get('category')}` {c.get('message')}"
            + (f" → {c['data']['status']}" if isinstance(c.get("data"), dict) and "status" in c["data"] else "")
            for c in crumbs[-15:]
        ]
        parts.append("**Trykkhistorikk (siste 15):**\n" + "\n".join(lines))

    if row.get("consent_screenshot"):
        where = f"{admin_base_url.rstrip('/')}/admin/reports/{report_id}/screenshot" if admin_base_url else \
            f"`GET …/admin/reports/{report_id}/screenshot`"
        parts.append(
            "**Skjermbilde (maskert)** ligger i løsningens egen database med TTL og er IKKE lastet "
            f"opp hit: {where}"
        )
    return "\n\n".join(parts)


class Triage:
    def __init__(self, store, tracker, llm, embedder, settings):
        self.store = store
        self.tracker = tracker
        self.llm = llm
        self.embedder = embedder
        self.s = settings
        # Tasks må holdes i live: asyncio kan søppelsamle en task ingen refererer til, og da
        # forsvinner triagen stille midt i.
        self._tasks = set()

    def schedule(self, coro_or_id):
        coro = self.run(coro_or_id) if isinstance(coro_or_id, int) else coro_or_id
        try:
            t = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return None
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def drain(self):
        """Vent til bakgrunnsjobbene er ferdige (tester, graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _classify(self, row):
        if not self.llm:
            return None
        text = f"Tittel: {row.get('title')}\nHva skjedde: {row.get('observed')}"
        if row.get("expected"):
            text += f"\nForventet: {row['expected']}"
        if row.get("steps"):
            text += "\nSteg: " + " → ".join(row["steps"])
        # Ikke-tekstlige metadata løfter treffsikkerheten på alvorlighet målbart.
        if row.get("route"):
            text += f"\nSide: {row['route']}"
        if row.get("blocking") is not None:
            text += f"\nStoppet brukeren: {'ja' if row['blocking'] else 'nei'}"
        if row.get("frequency"):
            text += f"\nFrekvens: {row['frequency']}"
        errs = [c for c in (row.get("breadcrumbs") or []) if isinstance(c, dict) and c.get("level") == "error"]
        if errs:
            text += "\nTekniske feil i sporet: " + "; ".join(
                f"{c.get('message')} ({(c.get('data') or {}).get('status', '')})" for c in errs[-3:]
            )
        return await self.llm.extract(
            classify_system(self.s.product_name, self.s.product_description, self.s.areas),
            text[:4000], _Classification, max_tokens=1000,
        )

    async def _judge(self, row, cand):
        """0–100. Ved feil: 0 — å IKKE slå sammen er alltid den trygge feilen."""
        if not self.llm:
            return 0
        text = (
            f"RAPPORT A (ny):\n{row.get('title')}\n{row.get('observed')}\n\n"
            f"RAPPORT B (#{cand['id']}, eksisterende):\n{cand.get('title')}\n{cand.get('observed')}"
        )
        v = await self.llm.extract(DUPE_SYSTEM, text[:4000], _DupeVerdict, max_tokens=1000)
        try:
            return max(0, min(100, int(v.confidence))) if v else 0
        except (TypeError, ValueError):
            return 0

    async def run(self, report_id):
        """Full triage av én rapport. Kaster aldri — rapporten ligger trygt og kan re-triageres."""
        try:
            await self._run(report_id)
        except Exception as e:
            print(f"meldfra: triage #{report_id} feilet (rapporten er lagret):", e)

    async def _run(self, report_id):
        st = self.store
        row = await asyncio.to_thread(st.get_report, report_id)
        if not row:
            return

        # 1. Embedding → 2. recall → KI-dommer
        canonical, conf = None, 0
        vec = await self.embedder.embed(build_text(row["title"], row["observed"], row["expected"])) \
            if self.embedder and self.embedder.available else None
        if vec:
            await asyncio.to_thread(st.set_embedding, report_id, vec)
            for cand in await asyncio.to_thread(st.find_similar, vec, report_id, self.s.dupe_recall, 5):
                c = await self._judge(row, cand)
                if c > conf:
                    conf, canonical = c, cand
                if conf >= DUPE_CERTAIN:
                    break
        is_dupe = bool(canonical) and conf >= DUPE_CERTAIN
        maybe = bool(canonical) and DUPE_MAYBE <= conf < DUPE_CERTAIN

        cluster_id = None
        if is_dupe:
            cluster_id = canonical.get("cluster_id") or canonical["id"]
            # Lagres FØR Reach telles — ellers teller ikke den nye rapportøren i sin egen klynge.
            await asyncio.to_thread(st.set_cluster, report_id, cluster_id)

        # 3. Klassifisering
        cls = await self._classify(row)
        severity = cls.severity if cls and cls.severity in SEVERITIES else "S3"
        area = cls.area if cls and cls.area in self.s.areas else "annet"
        reproducible = bool(cls.reproducible) if cls else False
        if row.get("kind") == "idea":
            severity = "S4"  # ideer prioriteres på Reach, ikke alvorlighet

        # 4. RICE
        reach = await asyncio.to_thread(st.cluster_reach, cluster_id or report_id)
        score = rice_score(reach, severity, reproducible, row.get("blocking"), row.get("frequency"))

        # 5. Tracker — ÉN sak per klynge
        number, url = None, None
        if is_dupe:
            number, url = canonical.get("issue_number"), canonical.get("issue_url")
            if number:
                # Labels på en eksisterende sak røres BEVISST ikke: et menneske kan ha triagert den.
                await self.tracker.comment(
                    number,
                    f"➕ Meldt på nytt (rapport #{report_id}) — **{reach} bruker(e)** har nå meldt "
                    f"denne feilen. RICE: {score}.\n\n_Duplikat-konfidens: {conf} %._",
                )
        else:
            body = issue_body(report_id, row, severity, area, score, reach, self.s.admin_base_url)
            if maybe:
                body += (f"\n\n**Mulig duplikat av rapport #{canonical['id']}** (konfidens {conf} %) — "
                         f"{canonical.get('issue_url') or 'ikke speilet til trackeren'}")
            prefix = "Idé" if row.get("kind") == "idea" else "Feil"
            issue = await self.tracker.create_issue(
                f"{prefix}: {row.get('title') or ''}"[:120], body,
                labels(row.get("kind"), severity, area, maybe_dupe=maybe),
            )
            if issue:
                number, url = issue.get("number"), issue.get("url")
                if maybe and canonical.get("issue_number"):
                    await self.tracker.comment(canonical["issue_number"], f"🔗 Mulig duplikat: {url} (konfidens {conf} %)")

        await asyncio.to_thread(
            st.set_triage, report_id, severity, area, cluster_id, score, self.tracker.name, number, url,
        )
        print(f"meldfra: triage #{report_id}: {severity}/{area} RICE={score} reach={reach} dupe={conf}%")
