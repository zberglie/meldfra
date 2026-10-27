"""Adaptere mot saksbehandlingsverktøyet (GitHub Issues, GitLab Issues).

Alle adaptere har samme fem operasjoner, og alle kaster ALDRI — en rapport ligger trygt i vår
egen database uansett, og en tracker som er nede skal ikke velte innsendingen.

  create_issue(title, body, labels) -> {"number", "url"} | None
  comment(number, body)             -> bool
  reopen(number)                    -> bool
  list_closed_since(iso)            -> [{"number", "url", "reason"}]   reason: "completed" | "not_planned"
  configured                        -> bool

VEDLEGG LASTES ALDRI OPP. Skjermbilder kan vise personopplysninger, og verken GitHub eller
GitLab har en TTL på opplastede filer — de blir liggende selv om saksteksten redigeres. Rå
bevis ligger i vår egen database med TTL; saken får bare en lenke til admin-visningen.
"""
import httpx

_TIMEOUT = httpx.Timeout(20.0)


class NullTracker:
    """Ingen tracker konfigurert: rapportene lagres og triageres, men speiles ikke ut."""

    name = "none"
    configured = False

    async def create_issue(self, title, body, labels):
        return None

    async def comment(self, number, body):
        return False

    async def reopen(self, number):
        return False

    async def list_closed_since(self, iso):
        return []


class GitHubTracker:
    """GitHub Issues. Bruk et PRIVAT repo — GitHub har ingen «confidential» per sak, så
    synligheten styres av repoet. Token: fine-grained PAT med Issues: read/write på ETT repo."""

    name = "github"

    def __init__(self, repo, token, api_url="https://api.github.com", transport=None):
        self.repo = repo
        self.token = token
        self.api = api_url.rstrip("/")
        self._transport = transport

    @property
    def configured(self):
        return bool(self.repo and self.token)

    def _client(self):
        return httpx.AsyncClient(
            base_url=f"{self.api}/repos/{self.repo}",
            timeout=_TIMEOUT,
            transport=self._transport,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def create_issue(self, title, body, labels):
        try:
            async with self._client() as c:
                # Labels som ikke finnes, opprettes av GitHub når tokenet har skrivetilgang.
                r = await c.post("/issues", json={"title": title, "body": body, "labels": labels})
            if r.status_code >= 300:
                print("meldfra/github: opprettelse ikke-OK:", r.status_code, r.text[:200])
                return None
            j = r.json()
            return {"number": j.get("number"), "url": j.get("html_url")}
        except Exception as e:
            print("meldfra/github: opprettelse feilet:", e)
            return None

    async def comment(self, number, body):
        try:
            async with self._client() as c:
                r = await c.post(f"/issues/{int(number)}/comments", json={"body": body})
            return r.status_code < 300
        except Exception as e:
            print("meldfra/github: kommentar feilet:", e)
            return False

    async def reopen(self, number):
        try:
            async with self._client() as c:
                r = await c.patch(f"/issues/{int(number)}", json={"state": "open"})
            return r.status_code < 300
        except Exception as e:
            print("meldfra/github: gjenåpning feilet:", e)
            return False

    async def list_closed_since(self, iso):
        """`since` filtrerer på sist OPPDATERT, ikke lukket — overlapp er ufarlig fordi
        statusoppdateringen er idempotent."""
        params = {"state": "closed", "since": iso, "per_page": "100", "sort": "updated", "direction": "desc"}
        try:
            async with self._client() as c:
                r = await c.get("/issues", params=params)
            if r.status_code >= 300:
                print("meldfra/github: list-closed ikke-OK:", r.status_code, r.text[:160])
                return []
            out = []
            for i in r.json() or []:
                if "pull_request" in i:  # issues-API-et returnerer også PR-er
                    continue
                out.append({
                    "number": i.get("number"),
                    "url": i.get("html_url"),
                    "reason": "not_planned" if i.get("state_reason") == "not_planned" else "completed",
                })
            return out
        except Exception as e:
            print("meldfra/github: list-closed feilet:", e)
            return []


class GitLabTracker:
    """GitLab Issues. Sakene opprettes som CONFIDENTIAL (synlige for Reporter+, ikke Guest)."""

    name = "gitlab"

    def __init__(self, api_url, token, project_id, host_header="", transport=None):
        self.api = (api_url or "").rstrip("/")
        self.token = token
        self.pid = project_id
        self.host_header = host_header
        self._transport = transport

    @property
    def configured(self):
        return bool(self.api and self.token and self.pid)

    def _client(self):
        h = {"PRIVATE-TOKEN": self.token}
        if self.host_header:
            # GitLab-nginx matcher på server_name; nås den internt, må Host-headeren stemme.
            h["Host"] = self.host_header
        return httpx.AsyncClient(
            base_url=f"{self.api}/projects/{self.pid}", timeout=_TIMEOUT,
            transport=self._transport, headers=h,
        )

    async def create_issue(self, title, body, labels):
        try:
            async with self._client() as c:
                r = await c.post("/issues", data={
                    "title": title, "description": body,
                    "labels": ",".join(labels), "confidential": "true",
                })
            if r.status_code >= 300:
                print("meldfra/gitlab: opprettelse ikke-OK:", r.status_code, r.text[:200])
                return None
            j = r.json()
            return {"number": j.get("iid"), "url": j.get("web_url")}
        except Exception as e:
            print("meldfra/gitlab: opprettelse feilet:", e)
            return None

    async def comment(self, number, body):
        try:
            async with self._client() as c:
                r = await c.post(f"/issues/{int(number)}/notes", data={"body": body})
            return r.status_code < 300
        except Exception as e:
            print("meldfra/gitlab: kommentar feilet:", e)
            return False

    async def reopen(self, number):
        try:
            async with self._client() as c:
                r = await c.put(f"/issues/{int(number)}", data={"state_event": "reopen"})
            return r.status_code < 300
        except Exception as e:
            print("meldfra/gitlab: gjenåpning feilet:", e)
            return False

    async def list_closed_since(self, iso):
        params = {"state": "closed", "updated_after": iso, "per_page": "100", "order_by": "updated_at", "sort": "desc"}
        try:
            async with self._client() as c:
                r = await c.get("/issues", params=params)
            if r.status_code >= 300:
                print("meldfra/gitlab: list-closed ikke-OK:", r.status_code, r.text[:160])
                return []
            return [
                {"number": i.get("iid"), "url": i.get("web_url"), "reason": "completed"}
                for i in (r.json() or []) if i.get("iid")
            ]
        except Exception as e:
            print("meldfra/gitlab: list-closed feilet:", e)
            return []


def from_settings(s):
    if s.tracker == "github":
        return GitHubTracker(s.github_repo, s.github_token, s.github_api_url)
    if s.tracker == "gitlab":
        return GitLabTracker(s.gitlab_api_url, s.gitlab_token, s.gitlab_project_id, s.gitlab_host_header)
    return NullTracker()
