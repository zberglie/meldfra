"""Tynn KI-klient: ett strukturert uttrekk per kall.

Modulen kaster ALDRI: feiler modellen, returnerer den None, og kalleren faller tilbake til
brukerens egne ord. En rapport skal aldri gå tapt fordi KI-en var nede.

Vil vertsløsningen bruke en annen leverandør, kan den sende inn sitt eget objekt med samme
`extract(system, user, schema)`-metode til MeldFra(llm=...).
"""
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class AnthropicLLM:
    def __init__(self, api_key, model, effort="low"):
        self.model = model
        self.effort = effort
        self._client = None
        if api_key:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=api_key, max_retries=2, timeout=30.0)

    @property
    def available(self):
        return self._client is not None

    async def extract(self, system: str, user: str, schema: Type[T], max_tokens=2000) -> Optional[T]:
        if not self._client:
            return None
        try:
            resp = await self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": self.effort},
            )
            if resp.stop_reason == "refusal":
                return None
            return resp.parsed_output
        except Exception as e:
            print("meldfra: KI-kall feilet (faller tilbake):", type(e).__name__, str(e)[:200])
            return None
