"""Sanering av rapporttekst før den lagres og før den forlater vår kontroll (tracker, LLM-triage).

HVORFOR: en bruker som melder en feil skriver naturlig «betalingen feilet, kortet mitt er
4925 0000 0000 0004» eller «e-posten min ola@nordmann.no får ikke kvittering». En tracker-sak
har ingen sletterutine, er søkbar og havner i backup — personopplysninger skal ikke ligge der,
og utvikleren trenger dem ikke for å fikse feilen.

DETTE ER ET ANDRELAG, IKKE ET FØRSTELAG. Førstelinjen er klienten (maskert skjermbilde,
trykkhistorikk med etiketter i stedet for verdier) og intervju-agenten (instruert til aldri å
gjengi personopplysninger). Denne modulen er nettet under — den fanger det brukeren likevel
skrev selv.

BEVISST ENKEL: deterministiske regex, ingen NER-modell. Microsoft Presidio er den naturlige
oppgraderingen når navne-/stedsgjenkjenning trengs; `scrub_text()` er sømmen den skal inn i.
Vertsløsningen kan legge til egne mønstre (f.eks. medlemsnummer) via `extra_patterns`.
"""
import re

# Norsk fødselsnummer / D-nummer: 11 siffer (evt. med mellomrom etter de 6 første).
_FNR = re.compile(r"\b\d{6}\s?\d{5}\b")
# Betalingskort: 13–19 siffer i grupper. Sjekkes FØR telefon, som ellers spiser sifrene.
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
# Norsk kontonummer: 1234.56.78901 / 1234 56 78901.
_ACCOUNT = re.compile(r"\b\d{4}[. ]\d{2}[. ]\d{5}\b")
# Telefonnummer (norsk, med/uten +47): 8 siffer i ALLE grupperinger — «91234567», «91 23 45 67»
# og mobilformatet «912 34 567». Et fast 2-2-2-2-mønster bommet på det siste.
_PHONE = re.compile(r"(?:\+47[\s-]?)?\b\d(?:[\s-]?\d){7}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Rekkefølgen betyr noe: lengste/mest spesifikke mønster først.
DEFAULT_PATTERNS = [
    (_EMAIL, "[e-post]"),
    (_ACCOUNT, "[kontonr]"),
    (_FNR, "[fnr]"),
    (_CARD, "[kortnr]"),
    (_PHONE, "[telefon]"),
]


class Scrubber:
    def __init__(self, extra_patterns=None):
        """extra_patterns: liste av (regex-streng eller kompilert mønster, erstatning).
        Kjøres FØR standardmønstrene, så et medlemsnummer ikke blir tolket som telefon."""
        extra = [
            (re.compile(p) if isinstance(p, str) else p, repl) for p, repl in (extra_patterns or [])
        ]
        self.patterns = extra + DEFAULT_PATTERNS

    def text(self, value):
        """Idempotent og trygg: returnerer alltid en streng, kaster aldri."""
        if not value:
            return ""
        try:
            s = str(value)
            for pat, repl in self.patterns:
                s = pat.sub(repl, s)
            return s
        except Exception as e:  # pragma: no cover — sanering skal aldri velte en rapport
            print("meldfra: sanering feilet (returnerer maskert):", e)
            return "[skjult]"


_default = Scrubber()


def scrub_text(value):
    return _default.text(value)
