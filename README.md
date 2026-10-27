# MeldFra

MeldFra er en «Meld fra»-knapp som kan bygges inn i en eksisterende nettløsning. Den har tre deler:

- **KI-intervju.** Brukeren skriver én setning. KI-en renskriver teksten, stiller høyst to faste oppfølgingsspørsmål og leverer et utkast som brukeren kan redigere før det sendes.
- **Personvern.** Skjermbildet sladdes i nettleseren, og trykkhistorikken viser bare hvilke knapper som ble trykket, ikke hva som sto på skjermen. Samtykket gis per vedlegg og per rapport. Fritekst saneres på serveren.
- **Speiling til trackeren.** Hver rapport blir en sak i GitHub eller GitLab Issues, med alvorlighet, område og RICE-prioritet. Duplikater samles i én sak. Når saken lukkes, får alle som meldte feilen beskjed, og de kan bekrefte at den er løst.

MeldFra er et bibliotek uten egen innlogging og uten avhengigheter til andre prosjekter. Vertsløsningen bestemmer hvem brukeren er. MeldFra står for resten.

```
nettleser                                server (vertsløsningen)                    tracker
┌──────────────────┐  POST /interview   ┌──────────────────────────┐
│ <meldfra-widget> │ ─────────────────► │ MeldFra-router (FastAPI) │
│  • trykkhistorikk│  POST /reports     │  • sanering              │  sak / kommentar  ┌──────────┐
│  • maskert bilde │ ─────────────────► │  • lagring (SQL)         │ ────────────────► │ GitHub / │
│  • 3-stegs ark   │  GET  /reports     │  • triage i bakgrunnen   │ ◄──────────────── │ GitLab   │
└──────────────────┘ ◄───────────────── │  • oppfølgingsløkke      │  lukkede saker    └──────────┘
                                        └──────────────────────────┘
```

## Innhold i repoet

| Mappe | Hva |
|---|---|
| `server/` | Python-pakken `meldfra`. Inneholder en FastAPI-router, lagring via SQLAlchemy (SQLite eller Postgres), KI-intervju, triage, tracker-adaptere og oppfølgingsløkka. Den ferdigbygde widgeten ligger i `server/meldfra/static/`. |
| `web/` | Kildekoden til widgeten i TypeScript, uten rammeverk og med Shadow DOM. `npm run build` lager både en `<script>`-fil og en ESM-modul. |
| `examples/` | En demoapp med en falsk medlemsportal. |
| `docs/ARKITEKTUR.md` | Designvalgene og begrunnelsene for dem. Les denne før du endrer noe. |

## Kom i gang

### 1. Backend (FastAPI)

```bash
pip install "git+https://github.com/zberglie/meldfra.git#subdirectory=server"   # + [postgres] for Postgres
```

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from meldfra import MeldFra, Settings

from portal.auth import current_user, require_admin   # portalens egne avhengigheter

meldfra = MeldFra(
    Settings.from_env(),          # leser MELDFRA_* fra miljøet, se .env.example
    get_user=current_user,        # → bruker-id, dict med "id", objekt med .id, eller None (gir 401)
    get_admin=require_admin,      # valgfri: gir admin-rutene (skjermbilder, liste, re-triage)
    notify=send_varsel,           # valgfri: (user_id, tittel, tekst, rapport), sync eller async
)

@asynccontextmanager
async def lifespan(app):
    await meldfra.start()         # oppretter tabellene og starter oppfølgingsløkka
    yield
    await meldfra.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(meldfra.router, prefix="/meldfra")
```

`get_user` er en vanlig FastAPI-avhengighet. Med Entra ID kan den for eksempel validere bearer-tokenet og returnere `oid`. MeldFra lagrer bare den id-en du gir den, ikke navn eller e-post.

Hvis du ikke sender inn `notify`, ser brukeren statusen i widgeten under «Mine rapporter». Knappen får en prikk når noe er fikset.

### 2. Frontend (én linje)

```html
<script src="/meldfra/meldfra.js" data-meldfra-auto data-lang="nb"></script>
```

Widgeten serveres av routeren, så den trenger verken CDN eller byggesteg. Endepunktet utledes av script-adressen. Du kan styre widgeten med disse data-attributtene:

| Attributt | Standard | Betydning |
|---|---|---|
| `data-lang` | `nb` | `nb` eller `en` |
| `data-position` | `bottom-left` | `bottom-left` eller `bottom-right` |
| `data-mask` | `marked` | `marked`: sladder bare `[data-meldfra-sensitive]` og skjemafelter. `all-text`: sladder all tekst |
| `data-show-button` | `true` | `false` skjuler den flytende knappen, og du kaller `MeldFra.open()` fra din egen meny |
| `data-hide-on` | (tom) | Kommaseparerte sti-prefikser der knappen skjules |
| `data-app-version` | (tom) | Vises i rapporten |
| `data-screenshot` | `true` | `false` slår av skjermbilde helt |

Bruker portalen et bearer-token i stedet for cookie, kan du initialisere widgeten fra kode:

```js
MeldFra.init({ endpoint: "/meldfra", headers: async () => ({ Authorization: `Bearer ${await getToken()}` }) });
MeldFra.open();        // fra en «Meld fra»-lenke
MeldFra.openMine();    // «Mine rapporter», for eksempel på profilsiden
MeldFra.reportError(err, "error-boundary");  // legger portalens egne feil inn i trykkhistorikken
```

### 3. Merk personopplysninger i portalen

```html
<div class="profilkort" data-meldfra-sensitive> … navn, adresse, medlemsnummer … </div>
<button data-testid="betal-kontingent">Betal</button>
```

- `data-meldfra-sensitive` sladder alt innhold i elementet i skjermbildet. Verdier i skjemafelter sladdes alltid. Er du i tvil, bruk `data-mask="all-text"`.
- `data-testid`, eventuelt `aria-label`, blir etiketten i trykkhistorikken. Uten den får knappen bare en generisk etikett. Synlig tekst brukes aldri som etikett.

### 4. Tema

Widgeten arver CSS-variabler fra `:root`: `--meldfra-primary`, `--meldfra-on-primary`, `--meldfra-surface`, `--meldfra-text`, `--meldfra-muted`, `--meldfra-field`, `--meldfra-border`, `--meldfra-radius`, `--meldfra-font` og `--meldfra-offset-bottom`. Mørk modus har egne `*-dark`-varianter.

## Oppsett av trackeren

**GitHub (anbefalt for et nytt prosjekt)**

1. Lag et **privat** repo for sakene, eller bruk portalens eget private repo. GitHub har ingen «confidential»-flagg per sak, så det er repoet som bestemmer hvem som ser sakene.
2. Lag et fine-grained personal access token med tilgang til **kun det repoet** og rettigheten **Issues: Read and write**. Legg det i `MELDFRA_GITHUB_TOKEN`.
3. Sett `MELDFRA_TRACKER=github` og `MELDFRA_GITHUB_REPO=eier/repo`.

Labels som ikke finnes, opprettes automatisk: `type/feil`, `type/idé`, `sev/S1–S4`, `prio/P1–P4`, `omr/<område>`, `meldfra` og `mulig-duplikat`.

En sak som lukkes som **«Completed»**, gir brukeren «Fikset! 🎉». En sak som lukkes som **«Not planned»**, gir en nøytral melding om at rapporten er vurdert. Melder brukeren «fortsatt feil», gjenåpnes saken med en kommentar.

**GitLab:** sett `MELDFRA_TRACKER=gitlab`, `MELDFRA_GITLAB_API_URL`, `MELDFRA_GITLAB_TOKEN` (project access token med api-scope) og `MELDFRA_GITLAB_PROJECT_ID`. Sakene opprettes som confidential.

**Ingen tracker:** rapportene lagres og triageres likevel og kan ses i `GET /meldfra/admin/reports`.

## HTTP-kontrakt

Alle rutene ligger under prefikset du velger, og alle krever `get_user`.

| Rute | Hva |
|---|---|
| `GET  /meldfra.js` | Widgeten |
| `POST /interview` | Én tilstandsløs tur i KI-intervjuet. Klienten holder på `answers` og `asked` mellom turene |
| `POST /reports` | Sender inn en godkjent rapport. Svarer `{ok, id}` med en gang, og triagen kjører i bakgrunnen |
| `GET  /reports` | «Mine rapporter» med status |
| `POST /reports/{id}/verify` | `{fixed: true/false}` for brukerens bekreftelse etter at saken er fikset |
| `DELETE /reports/{id}` | Trekker samtykket. Rapporten og vedleggene slettes |
| `GET  /admin/reports` | (admin) Liste, kan filtreres med `?status=` |
| `GET  /admin/reports/{id}/screenshot` | (admin) Det maskerte skjermbildet, så lenge TTL ikke har utløpt |
| `POST /admin/reports/{id}/retriage` | (admin) Kjører triagen på nytt |

**Hvis portalen ikke bruker FastAPI** (for eksempel .NET eller Node), kjør MeldFra som en egen liten tjeneste bak samme domene, for eksempel på `/meldfra` i reverse proxyen. Da validerer `get_user` det samme tokenet eller den samme sesjonscookien som portalen bruker. Widgeten merker ikke forskjell.

## Utvikling

```bash
python -m venv .venv && .venv/bin/pip install -e "server[dev]"
(cd server && ../.venv/bin/python -m pytest -q)        # 27 tester: personvern, intervju, triage, GitHub, sløyfa

(cd web && npm ci && npm run build)                    # typesjekk + bygg widgeten til server/meldfra/static/

.venv/bin/uvicorn examples.demo_app:app --port 8765    # demo på http://localhost:8765/kontingent
(cd web && npx playwright test)                        # ende-til-ende mot demoen
```

Den bygde widgeten ligger i git, slik at Python-pakken er komplett uten Node. CI feiler hvis `web/src` er endret uten at widgeten er bygget på nytt.

## Personvern i korte trekk

Detaljene står i `docs/ARKITEKTUR.md`.

| Data | Rettslig grunnlag | Standard | Hvor det havner |
|---|---|---|---|
| Rapporttekst (sanert) | berettiget interesse | alltid med | egen database og trackeren |
| Enhet, nettleser, rute | berettiget interesse | alltid med | egen database og trackeren |
| Trykkhistorikk (bare etiketter) | berettiget interesse | **på**, kan slås av | egen database og trackeren |
| Skjermbilde (maskert) | samtykke | **av** | **bare** i egen database, slettes etter 30 dager, aldri i trackeren |

Brukeren ser nøyaktig hva som sendes (bildet og hendelseslista) før noe forlater nettleseren. Brukeren kan slette sine egne rapporter i «Mine rapporter».

## Lisens

MIT. Se `LICENSE`.
