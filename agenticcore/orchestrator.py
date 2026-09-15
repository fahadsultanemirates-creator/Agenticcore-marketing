"""Coordinates the specialist agents into an end-to-end campaign plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from agenticcore.agents import (
    AgentResult,
    CampaignAnalystAgent,
    ContentStrategistAgent,
    CopywriterAgent,
    EmailMarketerAgent,
    SEOSpecialistAgent,
    SocialMediaManagerAgent,
)
from agenticcore.llm import LLMClient, default_llm_client

DEFAULT_CHANNELS = ("social", "email", "seo")


@dataclass
class CampaignBrief:
    """The inputs a human provides to kick off a campaign."""

    product: str
    audience: str
    goal: str
    tone: str = "confident and clear"
    channels: List[str] = field(default_factory=lambda: list(DEFAULT_CHANNELS))


@dataclass
class CampaignPlan:
    """The aggregated output of a full agent run."""

    brief: CampaignBrief
    results: Dict[str, AgentResult]

    def to_markdown(self) -> str:
        sections = [f"# Campaign plan: {self.brief.product}\n"]
        sections.append(f"**Audience:** {self.brief.audience}  ")
        sections.append(f"**Goal:** {self.brief.goal}  ")
        sections.append(f"**Tone:** {self.brief.tone}\n")
        titles = {
            "strategy": "Strategy",
            "copy": "Ad copy",
            "social": "Social posts",
            "email": "Email sequence",
            "seo": "SEO recommendations",
            "analysis": "KPIs & critique",
        }
        for key, result in self.results.items():
            sections.append(f"\n## {titles.get(key, key.title())}\n")
            sections.append(result.output)
        return "\n".join(sections)


class MarketingOrchestrator:
    """Runs a ``CampaignBrief`` through the relevant specialist agents.

    The pipeline is: a content strategist sets positioning and narrative,
    then channel specialists (copy, social, email, SEO) produce deliverables
    in parallel against that shared strategy, and finally a campaign analyst
    defines KPIs and critiques the full set of deliverables for consistency.
    """

    def __init__(self, llm: Optional[LLMClient] = None):
        llm = llm or default_llm_client()
        self.strategist = ContentStrategistAgent(llm)
        self.copywriter = CopywriterAgent(llm)
        self.seo = SEOSpecialistAgent(llm)
        self.social = SocialMediaManagerAgent(llm)
        self.email = EmailMarketerAgent(llm)
        self.analyst = CampaignAnalystAgent(llm)

    def run_campaign(self, brief: CampaignBrief) -> CampaignPlan:
        results: Dict[str, AgentResult] = {}

        results["strategy"] = self.strategist.run(
            task="Develop a marketing strategy and core narrative for this campaign.",
            context=asdict(brief),
        )

        shared_context = {**asdict(brief), "strategy": results["strategy"].output}

        results["copy"] = self.copywriter.run(
            "Write headline options, ad copy, and CTAs.", shared_context
        )

        if "social" in brief.channels:
            results["social"] = self.social.run(
                "Draft platform-native social posts.", shared_context
            )
        if "email" in brief.channels:
            results["email"] = self.email.run(
                "Draft a lifecycle email sequence.", shared_context
            )
        if "seo" in brief.channels:
            results["seo"] = self.seo.run(
                "Provide keyword and on-page SEO recommendations.", shared_context
            )

        analysis_context = {
            **shared_context,
            "deliverables": {key: result.output for key, result in results.items()},
        }
        results["analysis"] = self.analyst.run(
            "Define KPIs, a measurement plan, and critique the deliverables above for "
            "consistency and gaps.",
            analysis_context,
        )

        return CampaignPlan(brief=brief, results=results)
