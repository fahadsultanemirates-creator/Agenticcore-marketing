"""LLM client backends used by agents.

Agents depend on the small ``LLMClient`` protocol below rather than on any
specific vendor SDK, so the framework can run against a real model in
production and against a deterministic stand-in in tests and offline demos.
"""

from __future__ import annotations

import os
import textwrap
from typing import Protocol


class LLMClient(Protocol):
    """Minimal interface every agent backend must implement."""

    def complete(self, system: str, prompt: str) -> str:
        """Return a completion for ``prompt`` under the given ``system`` role."""
        ...


class AnthropicLLMClient:
    """Talks to the Claude API via the official ``anthropic`` SDK."""

    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 2048):
        import anthropic  # imported lazily so the dependency is optional for dry runs

        self.model = model or os.environ.get("AGENTICCORE_MODEL", "claude-sonnet-5")
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, prompt: str) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text").strip()


class EchoLLMClient:
    """Deterministic offline backend for tests, demos, and CI.

    It does not call any external service; it just reflects the request back
    in a readable form so the orchestrator's wiring can be exercised without
    an API key or network access.
    """

    def complete(self, system: str, prompt: str) -> str:
        return textwrap.dedent(
            f"""\
            [offline dry-run response]
            role: {system.splitlines()[0] if system else "unknown"}
            {prompt}"""
        ).strip()


def default_llm_client() -> LLMClient:
    """Pick a real backend when an API key is configured, else an offline one."""

    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLMClient()
    return EchoLLMClient()
