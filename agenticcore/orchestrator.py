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
    ReachCriticAgent,
    SEOSpecialistAgent,
    SocialMediaManagerAgent,
)
from agenticcore.agents.reach_critic import ReachVerdict
from agenticcore.llm import LLMClient, default_llm_client
from agenticcore.reach import brief_for_agent

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
        self.reach_critic = ReachCriticAgent(llm)

    def run_strategy(
        self,
        brief: CampaignBrief,
        performance: str = "",
    ) -> AgentResult:
        """Run only the strategist.

        Split out so callers that just need positioning — the publishing
        pipeline, which then drafts one post per page — can get it without
        paying for the copy, email, SEO, and analysis passes a full campaign
        plan includes.

        ``performance`` is the readout of how this brand's earlier posts did
        (see ``agenticcore.performance``). Passing it is what makes the
        system improve rather than restart: without it every campaign is the
        brand's first, forever.
        """

        context = asdict(brief)
        task = "Develop a marketing strategy and core narrative for this campaign."
        if performance:
            context["past_performance"] = performance
            task += (
                " Ground it in past_performance: lean into the angles and tones "
                "that earned engagement from this audience and move away from "
                "the ones that did not. Say briefly which past results you are "
                "reacting to and why."
            )
        return self.strategist.run(task=task, context=context)

    def draft_post(
        self,
        brief: CampaignBrief,
        strategy: str,
        platform: str,
        label: Optional[str] = None,
        recent_captions: Optional[List[str]] = None,
        keyword: Optional[str] = None,
    ) -> AgentResult:
        """Draft one publish-ready post for a single platform.

        The pipeline sends this string to a page verbatim, so the task names
        exactly one platform — that's what puts the social agent into its
        single-post mode (see ``SocialMediaManagerAgent``).

        ``recent_captions`` are this brand's last few published posts. The
        model cannot see its own history, so without them a schedule running
        weekly converges on the same handful of hooks — and the duplication
        is invisible until a reader notices it before you do.
        """

        destination = f"{label} ({platform})" if label else platform
        task = (
            f"Write one ready-to-publish post for {destination}. Return only the "
            f"post copy itself. Obey reach_rules exactly — the point of the post "
            f"is to be shown to people, and those rules are what decides that."
        )
        context = {
            **asdict(brief),
            "strategy": strategy,
            "platform": platform,
            "reach_rules": brief_for_agent(platform),
        }
        if keyword:
            context["target_search_keyword"] = keyword
            task += (
                f" Work the phrase '{keyword}' in naturally — it is what this "
                f"brand wants to be found for in in-platform search."
            )
        if recent_captions:
            context["already_published"] = "\n---\n".join(recent_captions)
            task += (
                " already_published lists what this brand posted recently. Do not "
                "reuse those hooks, opening lines, structures, or examples — take "
                "a genuinely different angle on the strategy."
            )
        return self.social.run(task, context)

    def critique_reach(
        self,
        caption: str,
        platform: str,
        keyword: Optional[str] = None,
    ) -> ReachVerdict:
        """Score a finished draft on whether it will travel, and rewrite it."""

        return self.reach_critic.critique(
            caption, platform, brief_for_agent(platform), keyword=keyword
        )

    def draft_video_script(self, brief: CampaignBrief, strategy: str) -> AgentResult:
        """Draft the words an avatar speaks in a short vertical video.

        Deliberately not the post caption. A caption is read; this is heard,
        so it gets no hashtags, no emoji, and no "link in bio" — HeyGen
        renders whatever comes back verbatim, stage directions included.
        """

        return self.social.run(
            "Write the spoken script for a 20-30 second vertical social video. "
            "Return only the words to be spoken: no scene directions, no "
            "speaker labels, no hashtags, no emoji, and no markdown.",
            {**asdict(brief), "strategy": strategy},
        )

    def run_campaign(self, brief: CampaignBrief) -> CampaignPlan:
        results: Dict[str, AgentResult] = {}

        results["strategy"] = self.run_strategy(brief)

        shared_context = {**asdict(brief), "strategy": results["strategy"].output}

        results["copy"] = self.copywriter.run(
            "Write headline options, ad copy, and CTAs.", shared_context
        )

        if "social" in brief.channels:
            results["social"] = self.social.run(
                "Draft platform-native social posts for LinkedIn, X (Twitter), and "
                "Instagram — one post per platform.",
                shared_context,
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
