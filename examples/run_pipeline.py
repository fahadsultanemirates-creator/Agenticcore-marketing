"""Generate a campaign for one brand, queue it on Telegram for approval, and
publish approved posts via Ayrshare as taps come in.

Usage:
    python examples/run_pipeline.py example          # queue + start listening
    python examples/run_pipeline.py example --dry-run  # no LLM/API calls at all

Requires (unless --dry-run):
    ANTHROPIC_API_KEY     Claude API key for the marketing agents
    TELEGRAM_BOT_TOKEN    from @BotFather
    TELEGRAM_CHAT_ID      default chat id (or set per-brand in brands/*.json)
    AYRSHARE_API_KEY      from your Ayrshare dashboard
Optional (for real generated images instead of text-only posts):
    IDEOGRAM_API_KEY  or  XAI_API_KEY
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Run straight from a clone — "python examples/run_pipeline.py" puts this
# file's directory on sys.path, not the repo root, so the package next door
# would otherwise be invisible. An installed copy ("pip install -e .") takes
# precedence; this only adds a fallback.
sys.path.append(str(Path(__file__).resolve().parent.parent))

from agenticcore.approval.telegram_bot import TelegramApprovalBot
from agenticcore.brands import BrandRegistry
from agenticcore.config import load_env
from agenticcore.creative.base import OfflineImageGenerator
from agenticcore.llm import EchoLLMClient
from agenticcore.orchestrator import MarketingOrchestrator
from agenticcore.pipeline import ContentPipeline
from agenticcore.publishing.ayrshare import AyrshareClient
from agenticcore.queue import DraftStore


def build_image_generator(dry_run: bool):
    if dry_run:
        return OfflineImageGenerator()
    if os.environ.get("IDEOGRAM_API_KEY"):
        from agenticcore.creative.ideogram import IdeogramImageClient

        return IdeogramImageClient()
    if os.environ.get("XAI_API_KEY"):
        from agenticcore.creative.grok import GrokImageClient

        return GrokImageClient()
    return None  # text-only posts


def main() -> None:
    load_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv or not os.environ.get("ANTHROPIC_API_KEY")
    if not args:
        print("Usage: python examples/run_pipeline.py <brand-slug> [--dry-run]", file=sys.stderr)
        raise SystemExit(1)
    brand_slug = args[0]

    brands = BrandRegistry("brands")
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient() if dry_run else None)
    image_generator = build_image_generator(dry_run)

    if dry_run:
        print("Running in --dry-run: skipping Telegram and Ayrshare, printing what would be sent.\n")
        pipeline_preview(brand_slug, brands, orchestrator, image_generator)
        return

    store = DraftStore("agenticcore.db")
    bot = TelegramApprovalBot()
    publisher = AyrshareClient()
    pipeline = ContentPipeline(brands, store, orchestrator, bot, publisher, image_generator)

    drafts = pipeline.queue_campaign(brand_slug)
    print(f"Queued {len(drafts)} draft(s) for '{brand_slug}' -> check Telegram to approve or reject.")

    print("Listening for Approve/Reject taps (Ctrl+C to stop)...")
    try:
        while True:
            for draft in pipeline.process_decisions(timeout=30):
                print(f"  {draft.id[:8]} [{draft.channel}] -> {draft.status}")
    except KeyboardInterrupt:
        print("\nStopped.")


def pipeline_preview(brand_slug, brands, orchestrator, image_generator) -> None:
    brand = brands.get(brand_slug)
    from agenticcore.orchestrator import CampaignBrief

    brief = CampaignBrief(
        product=brand.name, audience=brand.audience, goal=brand.goal or f"Grow {brand.name}",
        tone=brand.tone, channels=["social"],
    )
    strategy = orchestrator.run_strategy(brief).output
    image_url = image_generator.generate_image(f"Social graphic for {brand.name}") if image_generator else None

    for target in brand.channels:
        caption = orchestrator.draft_post(brief, strategy, target.channel, target.label).output
        print(f"--- would send to Telegram for approval: {brand.name} -> {target.label or target.channel} ---")
        print(caption)
        if image_url:
            print(f"[image: {image_url}]")
        print()


if __name__ == "__main__":
    main()
