"""Finds what a brand's audience is actually asking, before anything is written."""

from __future__ import annotations

from typing import Optional

from agenticcore.research.client import ResearchClient, ResearchResult
from agenticcore.research.opportunities import ContentOpportunity, parse_opportunities

DEMAND_SYSTEM = (
    "You are a demand researcher for a social media team. Your job is not to "
    "write anything. It is to find, with evidence from the live web, the "
    "specific questions this brand's buyers are actually asking — and to "
    "reject the ones that only sound plausible.\n\n"
    "Search before you answer. A question you assume is being asked is worth "
    "nothing; a question you found people asking is the whole deliverable.\n\n"
    "What makes an opportunity good:\n"
    "- It is phrased the way a real buyer would type or say it, not the way a "
    "marketer would title a blog post.\n"
    "- Someone is demonstrably asking it — a forum thread, a repeated search "
    "phrase, a comment section, a competitor's FAQ, a news event people are "
    "reacting to.\n"
    "- It is under-answered, or answered badly. A question every competitor "
    "already answers well is not an opportunity; a question they all dodge "
    "is the best kind.\n"
    "- This specific brand can answer it credibly.\n\n"
    "Reject: anything generic enough to apply to any company in the sector, "
    "anything you could have written without searching, and anything whose "
    "honest answer would be an ad.\n\n"
    "Reply with nothing but opportunity blocks in exactly this format:\n\n"
    "### OPPORTUNITY\n"
    "QUERY: <the question in the audience's own words>\n"
    "EVIDENCE: <where you saw it being asked, and why it is under-answered>\n"
    "ANGLE: <how this brand specifically should answer it, in one or two sentences>\n"
    "FORMAT: <post, video, thread, or carousel>\n\n"
    "If the searches genuinely turn up nothing worth posting about, reply "
    "with the single line NO OPPORTUNITIES FOUND. That is a valid and "
    "useful answer — inventing topics is the failure this role exists to "
    "prevent."
)


class DemandResearchAgent:
    """Turns a brand profile into researched, evidenced topics.

    Deliberately not a ``BaseAgent``: the others answer from their prompt,
    this one must go and look. It takes a ``ResearchClient`` instead of an
    ``LLMClient``, which is the whole distinction.
    """

    name = "demand_researcher"

    def __init__(self, research_client: ResearchClient):
        self.client = research_client

    def find(
        self,
        brand,
        how_many: int = 6,
        already_covered: Optional[list[str]] = None,
        max_searches: int = 6,
    ) -> tuple[list[ContentOpportunity], ResearchResult]:
        """Research ``how_many`` opportunities for this brand.

        ``already_covered`` are queries the brand has previously found, so
        weekly runs push into new territory instead of resurfacing the same
        evergreen questions every time.
        """

        prompt = [
            f"Brand: {brand.name}",
            f"What it does: {getattr(brand, 'description', '') or brand.goal or brand.name}",
            f"Target audience: {brand.audience}",
        ]
        if brand.goal:
            prompt.append(f"What its posts should drive: {brand.goal}")
        if getattr(brand, "keywords", None):
            prompt.append(f"Terms it wants to be found for: {', '.join(brand.keywords)}")

        if already_covered:
            prompt += [
                "",
                "Already found in earlier research — do NOT return these again, "
                "and do not return near-duplicates of them:",
                *(f"- {q}" for q in already_covered[:40]),
            ]

        prompt += [
            "",
            f"Search the web now and return up to {how_many} opportunities. "
            f"Prefer a smaller number of well-evidenced ones over filling the "
            f"quota with guesses.",
        ]

        result = self.client.research(
            DEMAND_SYSTEM, "\n".join(prompt), max_searches=max_searches
        )
        if "NO OPPORTUNITIES FOUND" in result.text.upper():
            return [], result

        return parse_opportunities(result.text, brand.slug, result.sources), result
