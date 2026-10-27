import json

import httpx
import pytest
from fastapi import FastAPI, Header

from meldfra import MeldFra, Settings, Store
from meldfra.interview import _Draft
from meldfra.triage import _Classification, _DupeVerdict


class FakeLLM:
    """Deterministisk stand-in for KI-en. `dupe` styrer dommerens konfidens."""

    def __init__(self, dupe=0, kind="bug", expected=None, fail=False):
        self.dupe = dupe
        self.kind = kind
        self.expected = expected
        self.fail = fail
        self.calls = []

    async def extract(self, system, user, schema, max_tokens=2000):
        self.calls.append((schema.__name__, user))
        if self.fail:
            return None
        if schema is _Draft:
            return _Draft(kind=self.kind, title="Renskrevet tittel", observed="Renskrevet tekst",
                          expected=self.expected, steps=["Åpne siden", "Trykk på knappen"])
        if schema is _Classification:
            return _Classification(severity="S2", area="betaling", reproducible=True)
        if schema is _DupeVerdict:
            return _DupeVerdict(confidence=self.dupe, reason="test")
        return None


class FakeEmbedder:
    available = True

    def __init__(self, vec=None):
        self.vec = vec or [1.0, 0.0, 0.0]

    async def embed(self, text):
        return list(self.vec)


class RecordingGitHub:
    """GitHub-API-et som en MockTransport — tester ekte HTTP-forming i GitHubTracker."""

    def __init__(self):
        self.issues = {}
        self.comments = []
        self.requests = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/issues"):
            body = json.loads(request.content)
            n = len(self.issues) + 1
            self.issues[n] = {**body, "number": n, "state": "open",
                              "html_url": f"https://github.com/o/r/issues/{n}"}
            return httpx.Response(201, json=self.issues[n])
        if request.method == "POST" and path.endswith("/comments"):
            self.comments.append((int(path.split("/")[-2]), json.loads(request.content)["body"]))
            return httpx.Response(201, json={})
        if request.method == "PATCH":
            n = int(path.split("/")[-1])
            self.issues[n].update(json.loads(request.content))
            return httpx.Response(200, json=self.issues[n])
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[i for i in self.issues.values() if i["state"] == "closed"])
        return httpx.Response(404)

    def close(self, n, reason="completed"):
        self.issues[n].update(state="closed", state_reason=reason)


def make_app(llm=None, embedder=None, tracker=None, notify=None, settings=None):
    s = settings or Settings(product_name="Medlemsportalen", product_description="medlemsportal",
                             areas=["betaling", "profil", "arrangement", "annet"], database_url="sqlite://",
                             server_version="abc123", admin_base_url="https://portal.example/meldfra")
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    store = Store("sqlite://", engine=create_engine("sqlite://", connect_args={"check_same_thread": False},
                                                     poolclass=StaticPool))
    store.create_tables()

    def get_user(x_user: str | None = Header(None)):
        return x_user

    def get_admin(x_admin: str | None = Header(None)):
        return x_admin == "ja"

    mf = MeldFra(s, get_user=get_user, get_admin=get_admin, notify=notify, llm=llm,
                 embedder=embedder or FakeEmbedder(), tracker=tracker, store=store)
    if llm is None:
        mf.llm = None
        mf.triage.llm = None
    app = FastAPI()
    app.include_router(mf.router, prefix="/meldfra")
    return app, mf


@pytest.fixture
def gh():
    from meldfra import GitHubTracker

    rec = RecordingGitHub()
    return rec, GitHubTracker("o/r", "tok", transport=httpx.MockTransport(rec.handler))


async def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
