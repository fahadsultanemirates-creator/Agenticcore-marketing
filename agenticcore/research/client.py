"""LLM calls that can look things up before answering.

The rest of the framework talks to ``LLMClient.complete``, which is a closed
box: the model answers from the prompt and nothing else. That is the right
shape for writing a post, and the wrong shape for deciding what the post
should be about — that decision needs evidence from outside.

``ResearchClient`` is the open-box counterpart. It runs Claude's server-side
web search tool, so the search happens on Anthropic's infrastructure rather
than needing a separate search vendor and key. Everything else in the
framework stays on the cheap closed-box path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Protocol

#: Claude's server-side web search tool. Supported on Sonnet 5 and the
#: Opus 4.6+ family; older models need the basic `web_search_20250305`
#: variant instead.
WEB_SEARCH_TOOL = "web_search_20260209"

#: Claude's server-side fetch tool. Only retrieves URLs already present in
#: the request, which is what we want: we name the pages to read.
WEB_FETCH_TOOL = "web_fetch_20260209"


@dataclass
class ResearchResult:
    """What a research call produced, with what it actually read."""

    text: str
    sources: list[str] = field(default_factory=list)
    searches_run: int = 0

    @property
    def is_grounded(self) -> bool:
        """Did this answer come from searches, or from the model's memory?

        Worth checking before acting on a result: an ungrounded answer is a
        guess wearing the costume of research, and for topic selection a
        confident guess is worse than no answer.
        """

        return self.searches_run > 0


class ResearchClient(Protocol):
    def research(self, system: str, prompt: str, max_searches: int = 5) -> ResearchResult:
        ...


class AnthropicResearchClient:
    """Runs a Claude call with web search enabled."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        max_tokens: int = 16000,
        allowed_domains: Optional[list[str]] = None,
        blocked_domains: Optional[list[str]] = None,
    ):
        import anthropic

        self.model = model or os.environ.get("AGENTICCORE_MODEL", "claude-sonnet-5")
        self.max_tokens = max_tokens
        self.allowed_domains = allowed_domains
        self.blocked_domains = blocked_domains
        self._client = anthropic.Anthropic(api_key=api_key)

    def _tool(self, max_searches: int) -> dict:
        tool: dict = {
            "type": WEB_SEARCH_TOOL,
            "name": "web_search",
            "max_uses": max_searches,
        }
        # The API rejects both filters at once; allowed wins if someone sets
        # both, since it is the stricter of the two.
        if self.allowed_domains:
            tool["allowed_domains"] = self.allowed_domains
        elif self.blocked_domains:
            tool["blocked_domains"] = self.blocked_domains
        return tool

    def read_pages(self, system: str, prompt: str, max_fetches: int = 8) -> ResearchResult:
        """Read named web pages, rather than searching for them.

        Fetching runs on Anthropic's servers, so this works from hosts whose
        own outbound access is restricted — the same reason web search does.
        """

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=[{"type": WEB_FETCH_TOOL, "name": "web_fetch", "max_uses": max_fetches}],
            messages=[{"role": "user", "content": prompt}],
        )

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        sources, fetched = [], 0
        for block in response.content:
            if block.type == "server_tool_use" and getattr(block, "name", "") == "web_fetch":
                fetched += 1
            elif block.type == "web_fetch_tool_result":
                # A failed fetch returns an error object where success
                # returns a result, and never raises.
                content = block.content
                url = getattr(content, "url", None)
                if url:
                    sources.append(url)

        return ResearchResult(text=text, sources=sources, searches_run=fetched)

    def research(self, system: str, prompt: str, max_searches: int = 5) -> ResearchResult:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=[self._tool(max_searches)],
            messages=[{"role": "user", "content": prompt}],
        )

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        sources: list[str] = []
        searches = 0
        for block in response.content:
            # The dynamic-filtering search tool also emits server_tool_use
            # blocks for the code execution it runs internally, so counting
            # every server tool call would overstate how much was searched —
            # and is_grounded would then vouch for an ungrounded answer.
            if block.type == "server_tool_use" and getattr(block, "name", "") == "web_search":
                searches += 1
            elif block.type == "web_search_tool_result":
                # A failed search returns an error OBJECT here where a
                # successful one returns a LIST, and never raises — so branch
                # on the shape rather than assuming results came back.
                content = block.content
                if isinstance(content, list):
                    sources += [
                        r.url for r in content
                        if getattr(r, "type", None) == "web_search_result"
                        and getattr(r, "url", None)
                    ]

        # Keep source order but drop repeats: one page cited by three
        # searches is still one piece of evidence.
        seen, unique = set(), []
        for url in sources:
            if url not in seen:
                seen.add(url)
                unique.append(url)

        return ResearchResult(text=text, sources=unique, searches_run=searches)


class OfflineResearchClient:
    """Deterministic stand-in so tests and dry runs never touch the network."""

    def __init__(self, text: str = ""):
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def read_pages(self, system: str, prompt: str, max_fetches: int = 8) -> ResearchResult:
        return self.research(system, prompt)

    def research(self, system: str, prompt: str, max_searches: int = 5) -> ResearchResult:
        self.calls.append((system, prompt))
        return ResearchResult(
            text=self.text or f"[offline research]\n{prompt[:400]}",
            sources=["offline://no-network"],
            searches_run=0,
        )


def default_research_client() -> ResearchClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicResearchClient()
    return OfflineResearchClient()
