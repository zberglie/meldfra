import base64

import pytest

from meldfra import Scrubber, scrub_text
from meldfra.interview import next_question, resolve_kind

from conftest import FakeEmbedder, FakeLLM, client_for, make_app

U1 = {"x-user": "medlem-1"}
U2 = {"x-user": "medlem-2"}
ADMIN = {"x-user": "admin", "x-admin": "ja"}
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


def report(**kw):
    return {"kind": "bug", "title": "Betaling feiler", "observed": "Kontingenten går ikke gjennom",
            "route": "/kontingent", "consent": {"screenshot": False, "breadcrumbs": True},
            "device": {"browser": "Firefox 140", "os": "Linux"}, **kw}


# ── Sanering ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,leak", [
    ("e-posten min ola@nordmann.no", "ola@nordmann.no"),
    ("fnr 01019012345 virker ikke", "01019012345"),
    ("ring meg på +47 912 34 567", "912 34 567"),
    ("kortet 4925 0000 0000 0004 ble avvist", "4925 0000 0000 0004"),
    ("konto 1234.56.78901", "1234.56.78901"),
])
def test_scrub_fjerner_personopplysninger(raw, leak):
    assert leak not in scrub_text(raw)


def test_scrub_lar_tekniske_tall_staa():
    # «feil 500» og «trykket 2 ganger» må bestå, ellers blir rapporten ubrukelig.
    s = "fikk feil 500 etter å ha trykket 2 ganger på side 3"
    assert scrub_text(s) == s


def test_scrub_egne_monstre():
    sc = Scrubber(extra_patterns=[(r"\bM-\d{5}\b", "[medlemsnr]")])
    assert sc.text("medlem M-12345 får ikke logget inn") == "medlem [medlemsnr] får ikke logget inn"


# ── Intervju-gaten ──────────────────────────────────────────────────────────────────────────

def test_gate_maks_to_sporsmal_og_ingen_for_ideer():
    assert next_question("idea", {}, [])is None
    assert next_question("bug", {}, [])["slot"] == "expected"
    assert next_question("bug", {"expected": "x"}, ["expected"])["slot"] == "blocking"
    assert next_question("bug", {}, ["expected", "blocking"]) is None


def test_ide_valg_vinner_men_forvalgt_bug_gir_seg_for_modellen():
    assert resolve_kind("idea", "bug") == "idea"
    assert resolve_kind("bug", "idea") == "idea"
    assert resolve_kind(None, None) == "bug"


async def test_intervju_uten_ki_gir_brukerens_egne_ord():
    app, _ = make_app()
    async with await client_for(app) as c:
        r = await c.post("/meldfra/interview", json={"observed": "Knappen gjør ingenting"}, headers=U1)
    assert r.status_code == 200
    assert r.json()["draft"]["observed"] == "Knappen gjør ingenting"
    assert r.json()["question"]["slot"] == "expected"


async def test_intervju_med_ki_og_brukerens_svar_vinner():
    app, _ = make_app(llm=FakeLLM(expected="modellens gjetning"))
    async with await client_for(app) as c:
        r = await c.post("/meldfra/interview", headers=U1, json={
            "observed": "Knappen gjør ingenting", "answers": {"expected": "at jeg kom til kassen"},
            "asked": ["expected"]})
    d = r.json()
    assert d["draft"]["title"] == "Renskrevet tittel"
    assert d["draft"]["expected"] == "at jeg kom til kassen"
    assert d["question"]["slot"] == "blocking"


async def test_krever_innlogging():
    app, _ = make_app()
    async with await client_for(app) as c:
        assert (await c.post("/meldfra/interview", json={"observed": "x"})).status_code == 401
        assert (await c.get("/meldfra/reports")).status_code == 401


# ── Innsending og personvern ────────────────────────────────────────────────────────────────

async def test_innsending_sanerer_og_respekterer_samtykke():
    app, mf = make_app()
    crumbs = [{"t": 1, "type": "tap", "category": "ui.tap", "message": "betal-knapp", "level": "info"}]
    async with await client_for(app) as c:
        r = await c.post("/meldfra/reports", headers=U1, json=report(
            observed="Kortet 4925 0000 0000 0004 ble avvist", breadcrumbs=crumbs, screenshot=PNG))
        rid = r.json()["id"]
        await mf.triage.drain()
        row = mf.store.get_report(rid)
    assert "4925" not in row["observed"]
    # Skjermbilde sendt UTEN samtykke → ikke lagret.
    assert row["consent_screenshot"] is False
    assert mf.store.get_attachment(rid, "screenshot") == (None, None)
    assert row["breadcrumbs"][0]["message"] == "betal-knapp"
    assert row["device"]["server_version"] == "abc123"


async def test_trykkhistorikk_avslaatt_lagres_ikke_selv_om_klienten_sender_den():
    app, mf = make_app()
    crumbs = [{"type": "tap", "category": "ui.tap", "message": "x"}]
    async with await client_for(app) as c:
        r = await c.post("/meldfra/reports", headers=U1, json=report(
            breadcrumbs=crumbs, consent={"breadcrumbs": False}))
    assert mf.store.get_report(r.json()["id"])["breadcrumbs"] is None


async def test_skjermbilde_med_samtykke_lagres_lokalt_men_aldri_i_saken(gh):
    rec, tracker = gh
    app, mf = make_app(llm=FakeLLM(), tracker=tracker)
    async with await client_for(app) as c:
        r = await c.post("/meldfra/reports", headers=U1, json=report(
            screenshot=PNG, consent={"screenshot": True}))
        await mf.triage.drain()
        rid = r.json()["id"]
        assert (await c.get(f"/meldfra/admin/reports/{rid}/screenshot", headers=U1)).status_code == 403
        shot = await c.get(f"/meldfra/admin/reports/{rid}/screenshot", headers=ADMIN)
    assert shot.status_code == 200 and shot.content.startswith(b"\x89PNG")
    body = rec.issues[1]["body"]
    assert "base64" not in body and "data:image" not in body
    assert "IKKE lastet opp" in body
    assert "https://portal.example/meldfra/admin/reports" in body


async def test_ikke_png_avvises_som_skjermbilde():
    app, mf = make_app()
    fake = "data:image/png;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode()
    async with await client_for(app) as c:
        r = await c.post("/meldfra/reports", headers=U1, json=report(screenshot=fake, consent={"screenshot": True}))
    assert mf.store.get_attachment(r.json()["id"], "screenshot") == (None, None)


# ── Triage og GitHub ────────────────────────────────────────────────────────────────────────

async def test_triage_oppretter_github_sak_med_labels(gh):
    rec, tracker = gh
    app, mf = make_app(llm=FakeLLM(), tracker=tracker)
    async with await client_for(app) as c:
        rid = (await c.post("/meldfra/reports", headers=U1, json=report())).json()["id"]
        await mf.triage.drain()
    issue = rec.issues[1]
    assert issue["title"] == "Feil: Betaling feiler"
    assert {"type/feil", "sev/S2", "prio/P2", "omr/betaling", "meldfra"} <= set(issue["labels"])
    assert rec.requests[0].headers["authorization"] == "Bearer tok"
    row = mf.store.get_report(rid)
    assert (row["status"], row["issue_number"], row["tracker"]) == ("triaged", 1, "github")


async def test_sikkert_duplikat_kommenterer_og_teller_reach(gh):
    rec, tracker = gh
    app, mf = make_app(llm=FakeLLM(dupe=95), tracker=tracker, embedder=FakeEmbedder())
    async with await client_for(app) as c:
        await c.post("/meldfra/reports", headers=U1, json=report())
        await mf.triage.drain()
        rid2 = (await c.post("/meldfra/reports", headers=U2, json=report(observed="Får ikke betalt"))).json()["id"]
        await mf.triage.drain()
    assert len(rec.issues) == 1, "duplikat skal aldri gi ny sak"
    assert rec.comments and "2 bruker(e)" in rec.comments[0][1]
    assert mf.store.get_report(rid2)["issue_number"] == 1


async def test_mulig_duplikat_far_egen_sak_og_kryss_lenke(gh):
    rec, tracker = gh
    app, mf = make_app(llm=FakeLLM(dupe=70), tracker=tracker)
    async with await client_for(app) as c:
        await c.post("/meldfra/reports", headers=U1, json=report())
        await mf.triage.drain()
        await c.post("/meldfra/reports", headers=U2, json=report())
        await mf.triage.drain()
    assert len(rec.issues) == 2
    assert "mulig-duplikat" in rec.issues[2]["labels"]
    assert rec.comments[0][0] == 1 and "Mulig duplikat" in rec.comments[0][1]


async def test_ide_far_s4():
    app, mf = make_app(llm=FakeLLM(kind="idea"))
    async with await client_for(app) as c:
        rid = (await c.post("/meldfra/reports", headers=U1, json=report(kind="idea"))).json()["id"]
        await mf.triage.drain()
    assert mf.store.get_report(rid)["severity"] == "S4"


# ── Oppfølgingssløyfa ───────────────────────────────────────────────────────────────────────

async def test_lukket_sak_varsler_alle_i_klyngen_en_gang(gh):
    rec, tracker = gh
    sent = []
    app, mf = make_app(llm=FakeLLM(dupe=95), tracker=tracker, notify=lambda u, t, x, r: sent.append((u, t)))
    async with await client_for(app) as c:
        await c.post("/meldfra/reports", headers=U1, json=report())
        await mf.triage.drain()
        await c.post("/meldfra/reports", headers=U2, json=report())
        await mf.triage.drain()
        rec.close(1)
        assert await mf.sync_now() == 2
        assert await mf.sync_now() == 0, "idempotent: ingen dobbeltvarsel"
        assert sorted(u for u, _ in sent) == ["medlem-1", "medlem-2"]
        assert sent[0][1] == "Fikset! 🎉"
        mine = (await c.get("/meldfra/reports", headers=U1)).json()["reports"]
    assert mine[0]["status"] == "fikset"


async def test_bekreftet_fredes_gjenapnet_varsles_paa_nytt(gh):
    rec, tracker = gh
    sent = []
    app, mf = make_app(llm=FakeLLM(), tracker=tracker, notify=lambda u, t, x, r: sent.append(u))
    async with await client_for(app) as c:
        rid = (await c.post("/meldfra/reports", headers=U1, json=report())).json()["id"]
        await mf.triage.drain()
        rec.close(1)
        await mf.sync_now()
        # Brukeren melder «fortsatt feil» → saken gjenåpnes på GitHub med kommentar.
        r = await c.post(f"/meldfra/reports/{rid}/verify", headers=U1, json={"fixed": False})
        assert r.json()["status"] == "gjenapnet"
        await mf.triage.drain()
        assert rec.issues[1]["state"] == "open"
        assert "fortsatt feil" in rec.comments[-1][1]
        rec.close(1)
        await mf.sync_now()
        assert sent == ["medlem-1", "medlem-1"]
        # Nå bekrefter brukeren — og pollingen skal ALDRI rulle det tilbake.
        assert (await c.post(f"/meldfra/reports/{rid}/verify", headers=U1, json={"fixed": True})).status_code == 200
        await mf.sync_now()
    assert mf.store.get_report(rid)["status"] == "bekreftet"
    assert len(sent) == 2


async def test_not_planned_blir_avvist_med_egen_tekst(gh):
    rec, tracker = gh
    sent = []
    app, mf = make_app(llm=FakeLLM(), tracker=tracker, notify=lambda u, t, x, r: sent.append(t))
    async with await client_for(app) as c:
        rid = (await c.post("/meldfra/reports", headers=U1, json=report())).json()["id"]
        await mf.triage.drain()
        rec.close(1, "not_planned")
        await mf.sync_now()
    assert mf.store.get_report(rid)["status"] == "avvist"
    assert sent == ["Vi har vurdert rapporten din"]


async def test_kan_ikke_kvittere_andres_rapport(gh):
    rec, tracker = gh
    app, mf = make_app(llm=FakeLLM(), tracker=tracker)
    async with await client_for(app) as c:
        rid = (await c.post("/meldfra/reports", headers=U1, json=report())).json()["id"]
        await mf.triage.drain()
        rec.close(1)
        await mf.sync_now()
        assert (await c.post(f"/meldfra/reports/{rid}/verify", headers=U2, json={"fixed": True})).status_code == 404
        assert (await c.delete(f"/meldfra/reports/{rid}", headers=U2)).status_code == 404


async def test_sletting_fjerner_rapport_og_vedlegg():
    app, mf = make_app()
    async with await client_for(app) as c:
        rid = (await c.post("/meldfra/reports", headers=U1, json=report(
            screenshot=PNG, consent={"screenshot": True}))).json()["id"]
        await mf.triage.drain()
        assert (await c.delete(f"/meldfra/reports/{rid}", headers=U1)).status_code == 200
        assert (await c.get("/meldfra/reports", headers=U1)).json()["reports"] == []
    assert mf.store.get_attachment(rid, "screenshot") == (None, None)


async def test_tracker_nede_velter_ikke_innsendingen():
    import httpx

    from meldfra import GitHubTracker

    down = GitHubTracker("o/r", "tok", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    app, mf = make_app(llm=FakeLLM(), tracker=down)
    async with await client_for(app) as c:
        r = await c.post("/meldfra/reports", headers=U1, json=report())
        await mf.triage.drain()
    assert r.json()["ok"] is True
    row = mf.store.get_report(r.json()["id"])
    assert row["status"] == "triaged" and row["issue_number"] is None


async def test_widget_serveres():
    app, _ = make_app()
    async with await client_for(app) as c:
        r = await c.get("/meldfra/meldfra.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
