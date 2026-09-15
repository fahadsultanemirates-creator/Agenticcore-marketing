"""Run a sample campaign through the AgenticCore orchestrator.

Uses the real Claude API if ANTHROPIC_API_KEY is set in the environment,
otherwise falls back to a deterministic offline client so you can see the
pipeline wiring without any credentials.

Usage:
    python examples/run_campaign.py
    python examples/run_campaign.py --dry-run
"""

from __future__ import annotations

import os
import sys

from agenticcore.llm import EchoLLMClient
from agenticcore.orchestrator import CampaignBrief, MarketingOrchestrator


def main() -> None:
    dry_run = "--dry-run" in sys.argv or not os.environ.get("ANTHROPIC_API_KEY")
    llm = EchoLLMClient() if dry_run else None

    orchestrator = MarketingOrchestrator(llm=llm)
    brief = CampaignBrief(
        product="AgenticCore, a multi-agent marketing automation framework",
        audience="Growth marketers and founders at early-stage B2B SaaS companies",
        goal="Drive qualified demo signups for the product launch",
        tone="confident, technical, no hype",
        channels=["social", "email", "seo"],
    )

    plan = orchestrator.run_campaign(brief)
    print(plan.to_markdown())

    if dry_run:
        print(
            "\n---\nNote: ran in offline dry-run mode (no ANTHROPIC_API_KEY set). "
            "Set the environment variable to generate real content.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
