"""Embeddings for duplikatdeteksjon — valgfritt.

Bruker et OpenAI-kompatibelt `/v1/embeddings`-endepunkt (Ollama, vLLM, LM Studio …). Rapport-
tekst kan inneholde personopplysninger selv etter saneringen; kjør derfor helst modellen i egen
infrastruktur. bge-m3 er flerspråklig og fungerer godt på norsk.

To fallgruver målt mot Ollama:
  1. Ollama TRUNKERER STILLE ved kontekstgrensen (200 OK, vektor av de første tokenene). Vi
     klipper derfor selv, så vi vet hva som faktisk ble embedded.
  2. Ollama gir bare dense-vektorer; ikke bygg noe som forutsetter hybrid-søk.
"""
import httpx

MAX_CHARS = 3000


def build_text(title, observed, expected=None):
    """Tittel + hva som skjedde + forventet — det er dét som gjør to rapporter til «samme feil».
    Enhetsinfo og trykkhistorikk holdes UTE: samme feil i to nettlesere skal havne i samme klynge."""
    return "\n".join(str(p) for p in (title, observed, expected) if p)[:MAX_CHARS]


class Embedder:
    def __init__(self, url, model, transport=None):
        self.url = url
        self.model = model
        self._transport = transport

    @property
    def available(self):
        return bool(self.url)

    async def embed(self, text):
        """Liste av float, eller None. Kaster aldri: uten vektor blir rapporten bare stående
        uten duplikatsjekk."""
        if not self.url or not text:
            return None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), transport=self._transport) as c:
                r = await c.post(self.url, json={"model": self.model, "input": str(text)[:MAX_CHARS]})
            if r.status_code >= 300:
                print("meldfra: embeddings ikke-OK:", r.status_code, r.text[:160])
                return None
            vec = ((r.json() or {}).get("data") or [{}])[0].get("embedding")
            return vec if isinstance(vec, list) and vec else None
        except Exception as e:
            print("meldfra: embeddings feilet (rapporten beholdes uten vektor):", e)
            return None
