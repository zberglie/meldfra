"""Innstillinger. Alt kan settes fra miljøvariabler (MELDFRA_*) eller direkte i kode.

Vertsløsningen eier ALT som er spesifikt for den: produktnavn, hvilke områder triagen kan
velge mellom, hvilken tracker sakene havner i. Modulen selv vet ingenting om løsningen den
bor i.
"""
import os
from dataclasses import dataclass, field


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_list(name, default):
    v = _env(name)
    return [s.strip() for s in v.split(",") if s.strip()] if v else list(default)


def _env_float(name, default):
    try:
        return float(_env(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # ── Produktet modulen bor i (brukes i promptene, aldri hardkodet) ───────────────────────
    product_name: str = "tjenesten"
    product_description: str = "en nettbasert tjeneste"
    # Områdene triagen kan plassere en sak i. Bli labels i trackeren: omr/<område>.
    areas: list = field(default_factory=lambda: ["annet"])

    # ── Lagring ────────────────────────────────────────────────────────────────────────────
    # Alt SQLAlchemy forstår: sqlite:///meldfra.db, postgresql+psycopg://…
    database_url: str = "sqlite:///meldfra.db"
    # Rå bevis (skjermbilder) slettes etter så mange dager. Rapportteksten beholdes.
    attachment_ttl_days: int = 30

    # ── Tracker ────────────────────────────────────────────────────────────────────────────
    tracker: str = "none"                 # "github" | "gitlab" | "none"
    github_repo: str = ""                 # "eier/repo"
    github_token: str = ""                # fine-grained PAT: Issues read/write på ETT repo
    github_api_url: str = "https://api.github.com"
    gitlab_api_url: str = ""              # https://gitlab.example.no/api/v4
    gitlab_token: str = ""                # project access token, api-scope
    gitlab_project_id: str = ""
    gitlab_host_header: str = ""          # kun når GitLab nås internt bak en vhost-proxy

    # ── KI ─────────────────────────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    # Intervju og triage er korte uttrekk/klassifiseringer — lav effort holder.
    llm_effort: str = "low"

    # ── Duplikatdeteksjon (valgfri) ────────────────────────────────────────────────────────
    # Et OpenAI-kompatibelt /v1/embeddings-endepunkt (Ollama, vLLM, …). Tomt = ingen dedupe.
    # Rapporttekst kan inneholde personopplysninger: kjør gjerne modellen lokalt.
    embeddings_url: str = ""
    embeddings_model: str = "bge-m3"
    dupe_recall: float = 0.65

    # ── Oppfølgingssløyfa ──────────────────────────────────────────────────────────────────
    poll_interval_s: int = 1800

    # ── Diverse ────────────────────────────────────────────────────────────────────────────
    # Lenke i saksteksten til admin-visningen av vedlegg, f.eks. https://portal.no/meldfra/admin
    admin_base_url: str = ""
    # Serverens versjon i rapportene (avslører «backend er ikke deployet ennå»).
    server_version: str = ""
    rate_limit_interview_per_min: int = 20
    rate_limit_submit_per_min: int = 6

    @classmethod
    def from_env(cls, **overrides):
        s = cls(
            product_name=_env("MELDFRA_PRODUCT_NAME", cls.product_name),
            product_description=_env("MELDFRA_PRODUCT_DESCRIPTION", cls.product_description),
            areas=_env_list("MELDFRA_AREAS", ["annet"]),
            database_url=_env("MELDFRA_DATABASE_URL", cls.database_url),
            attachment_ttl_days=int(_env("MELDFRA_ATTACHMENT_TTL_DAYS", cls.attachment_ttl_days)),
            tracker=(_env("MELDFRA_TRACKER", cls.tracker) or "none").lower(),
            github_repo=_env("MELDFRA_GITHUB_REPO", ""),
            github_token=_env("MELDFRA_GITHUB_TOKEN", ""),
            github_api_url=_env("MELDFRA_GITHUB_API_URL", cls.github_api_url),
            gitlab_api_url=_env("MELDFRA_GITLAB_API_URL", ""),
            gitlab_token=_env("MELDFRA_GITLAB_TOKEN", ""),
            gitlab_project_id=_env("MELDFRA_GITLAB_PROJECT_ID", ""),
            gitlab_host_header=_env("MELDFRA_GITLAB_HOST_HEADER", ""),
            anthropic_api_key=_env("MELDFRA_ANTHROPIC_API_KEY", _env("ANTHROPIC_API_KEY", "")),
            llm_model=_env("MELDFRA_LLM_MODEL", cls.llm_model),
            llm_effort=_env("MELDFRA_LLM_EFFORT", cls.llm_effort),
            embeddings_url=_env("MELDFRA_EMBEDDINGS_URL", ""),
            embeddings_model=_env("MELDFRA_EMBEDDINGS_MODEL", cls.embeddings_model),
            dupe_recall=_env_float("MELDFRA_DUPE_RECALL", cls.dupe_recall),
            poll_interval_s=int(_env("MELDFRA_POLL_INTERVAL_S", cls.poll_interval_s)),
            admin_base_url=_env("MELDFRA_ADMIN_BASE_URL", ""),
            server_version=_env("MELDFRA_SERVER_VERSION", _env("GIT_SHA", "")),
        )
        for k, v in overrides.items():
            setattr(s, k, v)
        if "annet" not in s.areas:
            s.areas.append("annet")
        return s
