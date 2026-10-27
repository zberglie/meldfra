"""KI-intervjuet: brukerens ene setning → en presis, redigerbar rapport.

ARKITEKTUR — SLOT-GATEN LIGGER I KODE, IKKE I PROMPTEN. LLM-er med rik kontekst lar
systematisk være å stille spørsmål, og manglende «forventet atferd» dikter de opp i stedet for
å oppdage at den mangler (arXiv 2605.25284, 2504.18804). Derfor:

  • KI-en gjør KUN det den er god til: renskrive brukerens tekst, klassifisere feil/idé og
    utlede reproduksjonssteg fra trykkhistorikken.
  • En ren Python-funksjon (next_question) avgjør hva som spørres om — med et hardt tak på
    2 spørsmål (fullføringsraten faller fra 83 % ved 1–3 spørsmål til 65 % ved 4–8).
  • Spørsmålene er FASTE tekster med hurtigsvar — deterministisk, aldri KI-formulert.

Flyten er STATELESS: klienten sender alt den har (tekst + svar + spurte slots) hver tur og får
enten ett spørsmål eller et ferdig utkast tilbake.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

MAX_QUESTIONS = 2
FREQUENCIES = ("forste-gang", "av-og-til", "hver-gang")


class _Draft(BaseModel):
    kind: Literal["bug", "idea"]
    title: str = Field(description="kort, konkret tittel på norsk, maks 80 tegn")
    observed: str = Field(description="hva som skjedde, renskrevet fra brukerens ord, 1-3 setninger")
    expected: Optional[str] = Field(
        None, description="hva brukeren FORVENTET — kun hvis brukeren selv har sagt det, ellers null"
    )
    steps: List[str] = Field(default_factory=list, description="maks 6 reproduksjonssteg")


def system_prompt(product_name, product_description):
    return (
        f"Du hjelper en bruker av {product_name} ({product_description}) å levere en presis "
        "rapport om en feil eller en idé. Du får brukerens egne ord, eventuelle svar på "
        "oppfølgingsspørsmål, og et teknisk interaksjonsspor (hva brukeren trykket på, hvilke "
        "kall som feilet).\n\n"
        "ABSOLUTTE REGLER:\n"
        "- expected: ALDRI gjett eller utled. Har ikke brukeren sagt hva hen forventet, er "
        "verdien null.\n"
        "- Gjengi ALDRI personopplysninger (navn på andre personer, fødselsnummer, kontonummer, "
        "kortnummer, telefon, e-post, adresser). Skriv [skjult] i stedet.\n"
        "- Men navn på funksjoner, sider, knapper, arrangementer eller produkter i tjenesten er "
        "ikke personopplysninger og er ofte hele feilen. BEHOLD dem slik brukeren skrev dem.\n"
        "- Behold brukerens mening; ikke legg til antakelser. Skriv norsk bokmål, nøkternt.\n"
        "- steps: maks 6 steg, kun det sporet eller teksten faktisk viser. Tom liste hvis "
        "ingenting kan utledes.\n"
        "- kind: 'idea' når brukeren ønsker seg noe nytt eller annerledes, 'bug' når noe ikke "
        "virker som det skal."
    )


QUESTIONS = {
    "expected": {"slot": "expected", "text": "Hva trodde du skulle skje?", "chips": []},
    "blocking": {
        "slot": "blocking",
        "text": "Kom du videre, eller stoppet det deg?",
        "chips": [
            {"label": "Jeg kom videre", "value": False},
            {"label": "Jeg måtte gi opp", "value": True},
        ],
    },
    "frequency": {
        "slot": "frequency",
        "text": "Har det skjedd før?",
        "chips": [
            {"label": "Første gang", "value": "forste-gang"},
            {"label": "Av og til", "value": "av-og-til"},
            {"label": "Hver gang", "value": "hver-gang"},
        ],
    },
}


def next_question(kind, slots, asked):
    """Den deterministiske gaten: hvilket spørsmål stilles nå — eller None (ferdig).

    Prioritet etter informasjonsgevinst: forventet atferd (kan ikke utledes) → blokkerende
    (alvorlighet, ett tapp) → frekvens (repro-sannsynlighet, ett tapp). Ideer får ingen
    spørsmål — problemet står i teksten.
    """
    if kind != "bug" or len(asked) >= MAX_QUESTIONS:
        return None
    if not slots.get("expected") and "expected" not in asked:
        return QUESTIONS["expected"]
    if slots.get("blocking") is None and "blocking" not in asked:
        return QUESTIONS["blocking"]
    if not slots.get("frequency") and "frequency" not in asked:
        return QUESTIONS["frequency"]
    return None


def resolve_kind(kind_hint, llm_kind):
    """Brukerens eksplisitte «idé» vinner alltid. «bug» er klientens FORVALG, som de fleste
    aldri rører — leser modellen teksten som en idé, vinner modellen, ellers får et ønske
    spørsmålet «kom du videre, eller stoppet det deg?»."""
    if kind_hint == "idea" or llm_kind == "idea":
        return "idea"
    return "bug"


def crumb_lines(breadcrumbs, limit=20):
    lines = []
    for c in (breadcrumbs or [])[-limit:]:
        if not isinstance(c, dict):
            continue
        status = ""
        if isinstance(c.get("data"), dict) and "status" in c["data"]:
            status = f" → {c['data']['status']}"
        lines.append(f"- {c.get('category')}: {c.get('message')}{status}")
    return "\n".join(lines) or "(ingen)"


async def interview_turn(llm, settings, observed, answers, asked, kind_hint=None, breadcrumbs=None, route=None):
    """Én stateless tur. Returnerer {"question": {...}, "draft": {...}} eller {"draft": {...}}.

    Utkastet bygges hver tur (klienten kan vise det underveis), og brukerens EGNE svar
    overstyrer alltid modellens utledning.
    """
    parts = [f"Brukerens ord: {observed[:2000]}"]
    if answers.get("expected"):
        parts.append(f"Brukerens svar på «hva trodde du skulle skje?»: {str(answers['expected'])[:500]}")
    if route:
        parts.append(f"Side/rute: {route}")
    parts.append(f"Interaksjonsspor (siste hendelser):\n{crumb_lines(breadcrumbs)}")

    extracted = None
    if llm is not None:
        extracted = await llm.extract(
            system_prompt(settings.product_name, settings.product_description),
            "\n\n".join(parts),
            _Draft,
        )
    ex = extracted.model_dump() if extracted else {}

    kind = resolve_kind(kind_hint, ex.get("kind"))
    slots = {
        "expected": (str(answers.get("expected") or "").strip() or None)
        or (str(ex.get("expected") or "").strip() or None),
        "blocking": answers.get("blocking") if isinstance(answers.get("blocking"), bool) else None,
        "frequency": answers.get("frequency") if answers.get("frequency") in FREQUENCIES else None,
    }
    steps = ex.get("steps") if isinstance(ex.get("steps"), list) else []
    draft = {
        "kind": kind,
        "title": (str(ex.get("title") or "").strip() or observed.split("\n")[0])[:120],
        "observed": (str(ex.get("observed") or "").strip() or observed)[:4000],
        "expected": slots["expected"],
        "steps": [str(s)[:200] for s in steps[:6]] or None,
        "blocking": slots["blocking"],
        "frequency": slots["frequency"],
    }
    q = next_question(kind, slots, asked or [])
    return {"question": q, "draft": draft} if q else {"draft": draft}
