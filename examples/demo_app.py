"""Demo: en liten «medlemsportal» med MeldFra montert. Kjør fra repo-roten:

    pip install -e "server[dev]"
    uvicorn examples.demo_app:app --reload --port 8765

Åpne http://localhost:8765. Innloggingen er falsk (cookie `demo_user`) — i en ekte portal er
`get_user` den eksisterende auth-avhengigheten (Entra ID, sesjon, …).

Uten MELDFRA_TRACKER lagres og triageres rapportene, men speiles ikke ut. Uten
ANTHROPIC_API_KEY hopper intervjuet over KI-en og bruker brukerens egne ord.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Cookie, FastAPI
from fastapi.responses import HTMLResponse

from meldfra import MeldFra, Settings


def current_user(demo_user: str | None = Cookie(None)):
    return demo_user or "demo-medlem"


def is_admin(demo_user: str | None = Cookie(None)):
    return (demo_user or "demo-medlem") == "demo-medlem"


def notify(user_id, title, text, report):
    # I portalen: send e-post eller vis et varsel i innboksen.
    print(f"[varsel til {user_id}] {title} — {text}")


settings = Settings.from_env(
    product_name="Medlemsportalen",
    product_description="medlemsportal for en forening: profil, kontingent, arrangementer",
    areas=["profil", "kontingent", "arrangement", "innlogging", "annet"],
    database_url="sqlite:///demo-meldfra.db",
)
meldfra = MeldFra(settings, get_user=current_user, get_admin=is_admin, notify=notify)


@asynccontextmanager
async def lifespan(app):
    await meldfra.start()
    yield
    await meldfra.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(meldfra.router, prefix="/meldfra")


@app.get("/", response_class=HTMLResponse)
@app.get("/kontingent", response_class=HTMLResponse)
async def page():
    return (Path(__file__).parent / "demo.html").read_text(encoding="utf-8")


@app.post("/api/betal")
async def pay():
    return HTMLResponse("Betalingstjenesten svarer ikke", status_code=502)
