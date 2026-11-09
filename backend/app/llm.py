from typing import Any, Protocol


class LLMProvider(Protocol):
    async def analyse(self, system_prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        """Return structured analysis. Implementations must not execute financial actions."""
        ...


class DisabledLLMProvider:
    """Default provider: no external model calls until explicitly configured."""

    async def analyse(self, system_prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("No LLM provider configured; deterministic processing remains available")


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible chat-completions adapter, activated only with credentials."""

    def __init__(self, base_url: str, api_key: str, model: str):
        if not api_key:
            raise ValueError("An API key is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def analyse(self, system_prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        import httpx

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": __import__("json").dumps(context)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        import json
        return json.loads(data["choices"][0]["message"]["content"])
