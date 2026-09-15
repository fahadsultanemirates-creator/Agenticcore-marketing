"""Generate campaigns, queue them on Telegram for approval, and publish the
approved ones via Ayrshare as taps come in.

Usage:
    python examples/run_pipeline.py <slug>           # queue that brand, then listen
    python examples/run_pipeline.py --all            # queue every brand, then listen
    python examples/run_pipeline.py --listen         # listen only, generate nothing
    python examples/run_pipeline.py <slug> --dry-run # preview, no network calls

--listen is what you want after a restart: it picks up drafts queued by an
earlier run that are still waiting on a tap, instead of generating a fresh
campaign nobody asked for.

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
    every_brand = "--all" in flags

    if not args and not (listen_only or every_brand):
        print(__doc__.split("Requires")[0].strip(), file=sys.stderr)
        raise SystemExit(1)

    brands = BrandRegistry("brands")
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient() if dry_run else None)
    image_generator = build_image_generator(dry_run)
    video_generator = build_video_generator(dry_run)

    if dry_run:
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
    bot = TelegramApprovalBot()
    publisher = AyrshareClient()
    pipeline = ContentPipeline(
        brands, store, orchestrator, bot, publisher, image_generator, video_generator
    )
    # Trust every brand's chat, not just the one we generate for: in --listen
    # the taps we're waiting on belong to drafts queued by an earlier run.
    pipeline.trust_configured_chats()

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
