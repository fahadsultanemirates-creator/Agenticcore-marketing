"""Generate campaigns, queue them on Telegram for approval, and publish the
approved ones via Ayrshare as taps come in.

Usage:
    python examples/run_pipeline.py <slug>           # queue that brand, then listen
    python examples/run_pipeline.py --all            # queue every brand, then listen
    python examples/run_pipeline.py --listen         # listen only, generate nothing
    python examples/run_pipeline.py --collect        # pull analytics, then exit
    python examples/run_pipeline.py --research <slug> # find topics worth posting about
    python examples/run_pipeline.py <slug> --dry-run # preview, no network calls

--listen is what you want after a restart: it picks up drafts queued by an
earlier run that are still waiting on a tap, instead of generating a fresh
campaign nobody asked for.

--research goes and looks: it searches the live web for the questions this
brand's buyers are actually asking, and queues them as content
opportunities. Campaigns then write about a researched question instead of
inventing a topic from the brand description. Run it weekly; each campaign
spends one opportunity, so keep the queue stocked.

--collect reads back how published posts performed and stores the numbers.
Run it on a schedule (daily is plenty — posts accrue views for days, and
each run refreshes in place). It is what closes the loop: the next campaign
feeds those results back into the strategy, so the system gets better at
this audience instead of starting from zero every week.

Requires (unless --dry-run):
    ANTHROPIC_API_KEY     Claude API key for the marketing agents
    TELEGRAM_BOT_TOKEN    from @BotFather
    TELEGRAM_CHAT_ID      default chat id (or set per-brand in brands/*.json)
    AYRSHARE_API_KEY      from your Ayrshare dashboard
Optional creative backends, picked up when a brand's "media" asks for them:
    IDEOGRAM_API_KEY or XAI_API_KEY                        ("media": "image")
    HEYGEN_API_KEY + HEYGEN_AVATAR_ID + HEYGEN_VOICE_ID    ("media": "video")
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
from agenticcore.brands import MEDIA_IMAGE, MEDIA_VIDEO, BrandRegistry
from agenticcore.config import load_env
from agenticcore.creative.base import OfflineImageGenerator, OfflineVideoGenerator
from agenticcore.llm import EchoLLMClient
from agenticcore.orchestrator import CampaignBrief, MarketingOrchestrator
from agenticcore.pipeline import ContentPipeline
from agenticcore.performance import MetricsStore, PerformanceMemory
from agenticcore.publishing.analytics import AyrshareAnalytics
from agenticcore.publishing.ayrshare import AyrshareClient
from agenticcore.research import (
    DemandResearchAgent,
    OpportunityStore,
    default_research_client,
)
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


def build_video_generator(dry_run: bool):
    if dry_run:
        return OfflineVideoGenerator()
    # All three are required: HeyGen needs to know which avatar speaks and in
    # which voice, and the client raises on a missing one.
    if all(os.environ.get(key) for key in
           ("HEYGEN_API_KEY", "HEYGEN_AVATAR_ID", "HEYGEN_VOICE_ID")):
        from agenticcore.creative.heygen import HeyGenVideoClient

        return HeyGenVideoClient()
    return None


def warn_about_unmet_media(brands: BrandRegistry, video_generator, image_generator) -> None:
    """Say so up front when a brand asks for creative we can't produce."""

    for brand in brands.all():
        if brand.media == MEDIA_VIDEO and video_generator is None:
            print(
                f"  note: brand '{brand.slug}' asks for video but HeyGen is not "
                f"configured (needs HEYGEN_API_KEY, HEYGEN_AVATAR_ID, "
                f"HEYGEN_VOICE_ID) — its posts will be text-only.",
                file=sys.stderr,
            )
        elif brand.media == MEDIA_IMAGE and image_generator is None:
            print(
                f"  note: brand '{brand.slug}' asks for an image but neither "
                f"IDEOGRAM_API_KEY nor XAI_API_KEY is set — its posts will be "
                f"text-only.",
                file=sys.stderr,
            )


def main() -> None:
    load_env()
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    dry_run = "--dry-run" in flags or not os.environ.get("ANTHROPIC_API_KEY")
    listen_only = "--listen" in flags
    collect_only = "--collect" in flags
    research_only = "--research" in flags
    every_brand = "--all" in flags

    if not args and not (listen_only or every_brand or collect_only or research_only):
        print(__doc__.split("Requires")[0].strip(), file=sys.stderr)
        raise SystemExit(1)

    brands = BrandRegistry("brands")

    if research_only:
        run_research(brands, args, dry_run)
        return

    orchestrator = MarketingOrchestrator(llm=EchoLLMClient() if dry_run else None)
    image_generator = build_image_generator(dry_run)
    video_generator = build_video_generator(dry_run)

    if dry_run:
        if collect_only:
            print("--collect needs real credentials: it reads live analytics from "
                  "Ayrshare and there is nothing to dry-run.", file=sys.stderr)
            return
        if listen_only and not args and not every_brand:
            print("--listen has nothing to preview in --dry-run: it generates no "
                  "campaigns, it waits on real Telegram taps.", file=sys.stderr)
            return
        print("Running in --dry-run: skipping Telegram and Ayrshare, printing what would be sent.\n")
        slugs = [b.slug for b in brands.all()] if every_brand else [args[0]]
        for slug in slugs:
            preview(slug, brands, orchestrator, image_generator, video_generator)
        return

    warn_about_unmet_media(brands, video_generator, image_generator)

    store = DraftStore("agenticcore.db")
    memory = PerformanceMemory(store, MetricsStore("agenticcore.db"))
    opportunities = OpportunityStore("agenticcore.db")
    bot = TelegramApprovalBot()
    publisher = AyrshareClient()
    pipeline = ContentPipeline(
        brands, store, orchestrator, bot, publisher, image_generator, video_generator,
        memory, AyrshareAnalytics(), True, opportunities,
    )
    # Trust every brand's chat, not just the one we generate for: in --listen
    # the taps we're waiting on belong to drafts queued by an earlier run.
    pipeline.trust_configured_chats()

    if collect_only:
        collected = pipeline.collect_metrics(args[0] if args else None)
        if not collected:
            print("No published posts with analytics yet.")
            return
        for m in sorted(collected, key=lambda m: m.engagement_rate, reverse=True):
            print(f"  {m.summary_line()}")
        measured = {m.brand_slug for m in collected}
        print(f"\nRecorded {len(collected)} post(s) across {len(measured)} brand(s). "
              f"The next campaign will use this.")
        return

    if listen_only:
        waiting = pipeline.pending_drafts()
        print(f"Listening only. {len(waiting)} draft(s) still awaiting approval.")
    elif every_brand:
        drafts = pipeline.queue_all()
        print(f"Queued {len(drafts)} draft(s) across {len(brands.all())} brand(s).")
    else:
        drafts = pipeline.queue_campaign(args[0])
        print(f"Queued {len(drafts)} draft(s) for '{args[0]}'.")

    print("Listening for Approve/Reject taps (Ctrl+C to stop)...")
    try:
        while True:
            for draft in pipeline.process_decisions(timeout=30):
                print(f"  {draft.id[:8]} [{draft.brand_slug}/{draft.channel}] -> {draft.status}")
    except KeyboardInterrupt:
        still_waiting = len(pipeline.pending_drafts())
        print(f"\nStopped. {still_waiting} draft(s) still pending — "
              f"resume with: python examples/run_pipeline.py --listen")


def run_research(brands: BrandRegistry, args: list[str], dry_run: bool) -> None:
    """Find and queue what this brand's audience is actually asking."""

    if dry_run:
        print("--research needs ANTHROPIC_API_KEY: it runs live web searches.",
              file=sys.stderr)
        return

    store = OpportunityStore("agenticcore.db")
    agent = DemandResearchAgent(default_research_client())
    slugs = args or [b.slug for b in brands.all()]

    for slug in slugs:
        brand = brands.get(slug)
        covered = [o.query for o in store.for_brand(slug)]
        print(f"Researching {brand.name} ({len(covered)} topic(s) already known)...")

        found, result = agent.find(brand, how_many=6, already_covered=covered)
        if not result.is_grounded:
            print("  WARNING: no searches ran — this answer is the model guessing, "
                  "not research. Not queueing it.", file=sys.stderr)
            continue
        if not found:
            print("  Nothing new worth posting about. That is a real answer, "
                  "not a failure.")
            continue

        fresh = store.add_all(found)
        print(f"  {result.searches_run} search(es), {len(result.sources)} source(s) "
              f"-> {len(fresh)} new opportunit{'y' if len(fresh) == 1 else 'ies'}:")
        for o in fresh:
            print(f"    - {o.query}")
            if o.angle:
                print(f"      angle: {o.angle}")

    total = sum(len(store.for_brand(s, status="open")) for s in slugs)
    print(f"\n{total} opportunit{'y' if total == 1 else 'ies'} queued. "
          f"Each campaign spends one.")


def preview(brand_slug, brands, orchestrator, image_generator, video_generator) -> None:
    brand = brands.get(brand_slug)
    brief = CampaignBrief(
        product=brand.name, audience=brand.audience, goal=brand.goal or f"Grow {brand.name}",
        tone=brand.tone, channels=["social"],
    )
    strategy = orchestrator.run_strategy(brief).output

    media_note = None
    if brand.media == MEDIA_VIDEO and video_generator is not None:
        script = orchestrator.draft_video_script(brief, strategy).output
        media_note = f"[video: {video_generator.generate_video(script)}]"
    elif brand.media == MEDIA_IMAGE and image_generator is not None:
        media_note = f"[image: {image_generator.generate_image(f'Social graphic for {brand.name}')}]"

    for target in brand.channels:
        caption = orchestrator.draft_post(brief, strategy, target.channel, target.label).output
        print(f"--- would send to Telegram for approval: {brand.name} -> {target.label or target.channel} ---")
        print(caption)
        if media_note:
            print(media_note)
        print()


if __name__ == "__main__":
    main()
