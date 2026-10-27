"""Lagring (SQLAlchemy Core — SQLite for enkel oppstart, Postgres i drift).

TO-LAGS LAGRING — dette er hele personvern-arkitekturen:
  meldfra_report      den KI-skrevne, BRUKERGODKJENTE, sanerte rapporten. KUN denne teksten
                      speiles til trackeren.
  meldfra_attachment  RÅ BEVIS (skjermbilder). Ligger KUN her, med TTL, og kommer ALDRI inn i
                      en tracker-sak (som verken har sletterutine eller tilgangskontroll på
                      vedleggsnivå).

Statusflyt: ny → triaged → fikset | avvist → bekreftet | gjenapnet (→ fikset igjen).

Alle funksjoner er synkrone; MeldFra kaller dem via asyncio.to_thread.
"""
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, MetaData, String,
    Table, Text, and_, create_engine, delete, func, or_, select, update,
)

metadata = MetaData()

report = Table(
    "meldfra_report", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(200), nullable=False, index=True),
    Column("kind", String(10), nullable=False, default="bug"),
    # Brukergodkjent innhold (det eneste som speiles ut)
    Column("title", Text, nullable=False),
    Column("observed", Text),
    Column("expected", Text),
    Column("steps", JSON),
    Column("frequency", String(20)),
    Column("blocking", Boolean),
    # Automatisk kontekst — ingen personopplysninger
    Column("route", String(200)),
    Column("app_version", String(120)),
    Column("device", JSON),
    Column("breadcrumbs", JSON),
    # Samtykke lagres PER RAPPORT: dokumentasjonen av at det faktisk ble gitt.
    Column("consent_screenshot", Boolean, nullable=False, default=False),
    Column("consent_breadcrumbs", Boolean, nullable=False, default=True),
    # Triage
    Column("embedding", JSON),
    Column("cluster_id", Integer, index=True),
    Column("severity", String(4)),
    Column("area", String(60)),
    Column("rice_score", Float),
    Column("status", String(20), nullable=False, default="ny", index=True),
    Column("tracker", String(20)),
    Column("issue_number", Integer, index=True),
    Column("issue_url", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)),
)

attachment = Table(
    "meldfra_attachment", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("report_id", Integer, ForeignKey("meldfra_report.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("kind", String(30), nullable=False),
    Column("mime", String(80)),
    Column("content", LargeBinary),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)),
)

# Statuser pollingen ikke skal røre: allerede varslet, eller kvittert ut av brukeren selv.
# Uten fredningen av 'bekreftet' ville neste runde rullet brukerens kvittering tilbake og sendt
# varselet på nytt. 'gjenapnet' skal derimot BEVISST varsles igjen når saken lukkes på nytt.
_FROZEN = ("fikset", "bekreftet", "avvist")


def _now():
    return datetime.now(timezone.utc)


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Store:
    def __init__(self, database_url, engine=None):
        kw = {}
        if database_url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
        self.engine = engine or create_engine(database_url, future=True, **kw)

    def create_tables(self):
        metadata.create_all(self.engine)

    # ── Innsending ─────────────────────────────────────────────────────────────────────────
    def insert_report(self, user_id, row):
        with self.engine.begin() as c:
            r = c.execute(report.insert().values(user_id=str(user_id), status="ny", **row))
            return r.inserted_primary_key[0]

    def insert_attachment(self, report_id, kind, content, mime, ttl_days):
        with self.engine.begin() as c:
            c.execute(attachment.insert().values(
                report_id=report_id, kind=kind, mime=mime, content=content,
                expires_at=_now() + timedelta(days=int(ttl_days)),
            ))

    def get_attachment(self, report_id, kind):
        with self.engine.connect() as c:
            r = c.execute(
                select(attachment.c.content, attachment.c.mime)
                .where(attachment.c.report_id == report_id, attachment.c.kind == kind,
                       attachment.c.expires_at > _now())
                .order_by(attachment.c.id.desc()).limit(1)
            ).first()
        return (bytes(r[0]), r[1]) if r else (None, None)

    def purge_expired_attachments(self):
        """Lovpålagt dataminimering, ikke husholdning (GDPR art. 5(1)(e))."""
        with self.engine.begin() as c:
            return c.execute(delete(attachment).where(attachment.c.expires_at <= _now())).rowcount or 0

    # ── Triage ─────────────────────────────────────────────────────────────────────────────
    def get_report(self, report_id):
        with self.engine.connect() as c:
            r = c.execute(select(report).where(report.c.id == report_id)).mappings().first()
        return dict(r) if r else None

    def set_embedding(self, report_id, vec):
        with self.engine.begin() as c:
            c.execute(update(report).where(report.c.id == report_id).values(embedding=vec))

    def find_similar(self, vec, exclude_id, min_similarity, limit=5, scan=2000):
        """Vektor-recall i Python: holder lenge for en medlemsportal (< ~10k rapporter), og
        krever ingen pgvector. Blir det tregt, er det denne funksjonen som skal byttes ut."""
        with self.engine.connect() as c:
            rows = c.execute(
                select(report.c.id, report.c.title, report.c.observed, report.c.cluster_id,
                       report.c.issue_number, report.c.issue_url, report.c.embedding)
                .where(report.c.embedding.is_not(None), report.c.id != exclude_id)
                .order_by(report.c.id.desc()).limit(scan)
            ).mappings().all()
        scored = []
        for r in rows:
            emb = r["embedding"]
            if not isinstance(emb, list) or len(emb) != len(vec):
                continue
            sim = _cosine(vec, emb)
            if sim >= min_similarity:
                d = {k: r[k] for k in ("id", "title", "observed", "cluster_id", "issue_number", "issue_url")}
                d["similarity"] = sim
                scored.append(d)
        scored.sort(key=lambda d: d["similarity"], reverse=True)
        return scored[:limit]

    def set_cluster(self, report_id, cluster_id):
        with self.engine.begin() as c:
            c.execute(update(report).where(report.c.id == report_id).values(cluster_id=cluster_id))

    def cluster_reach(self, cluster_id):
        """Distinkte brukere i klyngen = «Reach» i RICE. Én bruker som melder tre ganger
        løfter ikke prioriteten — tre ulike brukere gjør det."""
        with self.engine.connect() as c:
            n = c.execute(
                select(func.count(func.distinct(report.c.user_id)))
                .where(or_(report.c.id == cluster_id, report.c.cluster_id == cluster_id))
            ).scalar()
        return max(1, int(n or 1))

    def set_triage(self, report_id, severity, area, cluster_id, rice, tracker, number, url):
        values = dict(severity=severity, area=area, cluster_id=cluster_id, rice_score=rice, status="triaged")
        if number:
            values.update(tracker=tracker, issue_number=number, issue_url=url)
        with self.engine.begin() as c:
            c.execute(update(report).where(report.c.id == report_id, report.c.status == "ny").values(**values))

    # ── Oppfølgingssløyfa ──────────────────────────────────────────────────────────────────
    def mark_closed(self, tracker, number, reason="completed"):
        """Alle rapporter knyttet til en lukket sak → 'fikset' (eller 'avvist' når saken ble
        lukket som «not planned»). Returnerer KUN radene som faktisk skiftet status, så
        pollingen kan kjøre så ofte den vil uten å varsle to ganger."""
        new_status = "avvist" if reason == "not_planned" else "fikset"
        changed = []
        with self.engine.begin() as c:
            rows = c.execute(
                select(report.c.id, report.c.user_id, report.c.title, report.c.kind)
                .where(report.c.tracker == tracker, report.c.issue_number == int(number),
                       report.c.status.not_in(_FROZEN))
            ).mappings().all()
            for r in rows:
                n = c.execute(
                    update(report).where(report.c.id == r["id"], report.c.status.not_in(_FROZEN))
                    .values(status=new_status)
                ).rowcount
                if n:
                    changed.append({**dict(r), "status": new_status})
        return changed

    def verify(self, report_id, user_id, fixed):
        """Brukeren kvitterer ut (eller avkrefter) en fiks. Kun egen rapport, kun fra 'fikset'.
        Returnerer (endret, tracker, issue_number). NB: en rapport UTEN sak er fortsatt en
        gyldig kvittering — `endret` og `issue_number` må ikke blandes."""
        with self.engine.begin() as c:
            r = c.execute(
                select(report.c.tracker, report.c.issue_number)
                .where(report.c.id == report_id, report.c.user_id == str(user_id), report.c.status == "fikset")
            ).first()
            if not r:
                return False, None, None
            c.execute(update(report).where(report.c.id == report_id)
                      .values(status="bekreftet" if fixed else "gjenapnet"))
            return True, r[0], r[1]

    def list_for_user(self, user_id, limit=50):
        cols = [report.c.id, report.c.kind, report.c.title, report.c.status, report.c.created_at,
                report.c.observed, report.c.expected, report.c.steps]
        with self.engine.connect() as c:
            rows = c.execute(
                select(*cols).where(report.c.user_id == str(user_id))
                .order_by(report.c.id.desc()).limit(int(limit))
            ).mappings().all()
        return [{**dict(r), "created_at": r["created_at"].isoformat() if r["created_at"] else None} for r in rows]

    def delete_report(self, report_id, user_id):
        """Brukeren trekker samtykket (art. 7(3)): rapport og vedlegg slettes. Vedleggene
        slettes eksplisitt — SQLite håndhever ikke ON DELETE CASCADE uten PRAGMA. Returnerer
        True hvis noe ble slettet."""
        with self.engine.begin() as c:
            owned = c.execute(
                select(report.c.id).where(report.c.id == report_id, report.c.user_id == str(user_id))
            ).first()
            if not owned:
                return False
            c.execute(delete(attachment).where(attachment.c.report_id == report_id))
            # Var dette klyngens kanoniske rapport, beholder de andre sin sak — bare pekeren går.
            c.execute(update(report).where(report.c.cluster_id == report_id).values(cluster_id=None))
            c.execute(delete(report).where(report.c.id == report_id))
            return True

    # ── Admin ──────────────────────────────────────────────────────────────────────────────
    def admin_list(self, status=None, limit=100):
        cols = [c for c in report.c if c.name not in ("embedding", "breadcrumbs")]
        q = select(*cols).order_by(report.c.id.desc()).limit(int(limit))
        if status:
            q = q.where(report.c.status == status)
        with self.engine.connect() as c:
            rows = c.execute(q).mappings().all()
        return [{**dict(r), "created_at": r["created_at"].isoformat() if r["created_at"] else None} for r in rows]

    def has_attachment(self, report_id, kind):
        with self.engine.connect() as c:
            return c.execute(
                select(attachment.c.id).where(and_(
                    attachment.c.report_id == report_id, attachment.c.kind == kind,
                    attachment.c.expires_at > _now()))
            ).first() is not None
