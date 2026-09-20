"""Post creation studio — no publishing.

Telegram bot (menus for site, count, text/video, topic):
    python examples/run_studio.py --bot

One-off from the command line:
    python examples/run_studio.py agenticcore --posts 3
    python examples/run_studio.py agenticcore --posts 3 --platform telegram
    python examples/run_studio.py agenticcore --video 2 --seconds 10
    python examples/run_studio.py agenticcore --posts 3 --topic "Weekend: 30% off"

Needs ANTHROPIC_API_KEY. The bot also needs TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID — only that chat can drive it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from agenticcore.brands import BrandRegistry
from agenticcore.config import load_env
from agenticcore.control import ControlBot, TelegramTransport
from agenticcore.orchestrator import MarketingOrchestrator
from agenticcore.queue import DraftStore
from agenticcore.research import (
    OpportunityStore, SignalStore, TerritoryStore, WebsiteStore,
)
from agenticcore.studio import PostStudio
from agenticcore.topics import TopicLedger

DB = "agenticcore.db"


def build_studio() -> tuple[PostStudio, BrandRegistry]:
    brands = BrandRegistry("brands")
    signals = SignalStore(DB)
    signals.expire_stale()
    studio = PostStudio(
        brands, MarketingOrchestrator(),
        websites=WebsiteStore(DB), store=DraftStore(DB),
        opportunities=OpportunityStore(DB), territory=TerritoryStore(DB),
        signals=signals, ledger=TopicLedger(DB),
    )
    return studio, brands


def flag_value(argv: list[str], flag: str, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


def main() -> None:
    load_env()
    argv = sys.argv[1:]

    if "--bot" in argv:
        studio, brands = build_studio()
        transport = TelegramTransport()
        if not transport.authorized:
            print("Set TELEGRAM_CHAT_ID — the bot refuses every chat otherwise.",
                  file=sys.stderr)
            raise SystemExit(1)
        bot = ControlBot(studio, brands, send=transport.send)
        try:
            transport.run(bot)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    slugs = [a for a in argv if not a.startswith("--")
             and a not in {flag_value(argv, f) for f in ("--posts", "--video", "--seconds",
                                                         "--platform", "--topic")}]
    if not slugs:
        print(__doc__.strip(), file=sys.stderr)
        raise SystemExit(1)

    studio, _ = build_studio()
    slug = slugs[0]
    topic = flag_value(argv, "--topic")

    if "--video" in argv:
        batch = studio.make_videos(
            slug, count=int(flag_value(argv, "--video", 1)),
            seconds=int(flag_value(argv, "--seconds", 30)), topic=topic,
        )
        for package in batch.videos:
            for message in package.as_telegram_messages():
                print(message, "\n")
            for warning in package.warnings():
                print(f"  heads up: {warning}")
    else:
        batch = studio.make_posts(
            slug, count=int(flag_value(argv, "--posts", 3)),
            platform=flag_value(argv, "--platform"), topic=topic,
        )
        for i, post in enumerate(batch.posts, 1):
            print(post.as_telegram_message(i, len(batch.posts)), "\n")

    print(f"{len(batch)} item(s). Nothing was published.")


if __name__ == "__main__":
    main()
