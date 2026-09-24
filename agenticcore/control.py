"""Menu-driven Telegram control for the post studio.

The approval bot answered one question — yes or no on a finished post. This
one drives the studio: which site, how many, text or video, what about. The
difference in shape is why it is a separate module rather than more
callbacks bolted onto the approval bot.

Navigation state lives per chat. Telegram gives you 64 bytes of callback
data, which is not enough to carry a site slug, a count, a format and a
topic through four menus — so the callback carries only a short action and
the choices accumulate in a Selection the bot holds.

Nothing here publishes. The bot generates and hands you the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

#: Offered counts. Small numbers first because three good posts you will
#: actually read beats ten you skim — the point of a batch is choice, not
#: volume, and a batch too big to read carefully is worse than a small one.
POST_COUNTS = (1, 3, 5, 10)
VIDEO_COUNTS = (1, 2, 3)
VIDEO_LENGTHS = (10, 30, 60)


@dataclass
class Selection:
    """What a chat has chosen so far."""

    brand_slug: Optional[str] = None
    kind: str = "text"           # text | video
    count: int = 3
    seconds: int = 30
    platform: Optional[str] = None
    topic: Optional[str] = None
    awaiting_topic: bool = False

    def summary(self, brand_name: str = "") -> str:
        parts = [brand_name or self.brand_slug or "no site"]
        if self.kind == "video":
            parts.append(f"{self.count} × {self.seconds}s video")
        else:
            parts.append(f"{self.count} post(s)")
            parts.append(self.platform or "across text pages")
        parts.append(f"topic: {self.topic}" if self.topic else "topic: framework decides")
        return " · ".join(parts)


def button(text: str, action: str) -> dict:
    return {"text": text, "callback_data": action}


def rows(*button_rows) -> dict:
    return {"inline_keyboard": [list(r) for r in button_rows]}


class ControlBot:
    """Menus over the studio. Transport-agnostic so it can be tested."""

    def __init__(self, studio, brands, send: Callable[[str, str, Optional[dict]], None]):
        self.studio = studio
        self.brands = brands
        self.send = send
        self.selections: dict[str, Selection] = {}

    def selection(self, chat_id: str) -> Selection:
        return self.selections.setdefault(chat_id, Selection())

    # -- menus ---------------------------------------------------------

    def main_menu(self, chat_id: str) -> None:
        sel = self.selection(chat_id)
        brand = self.brands.get(sel.brand_slug) if sel.brand_slug else None
        name = brand.name if brand else ""

        self.send(
            chat_id,
            f"AgenticCore studio\n\n{sel.summary(name)}",
            rows(
                [button("Switch site", "menu:site")],
                [button("Text posts", "kind:text"), button("Video", "kind:video")],
                [button("How many", "menu:count"),
                 button("Which page", "menu:platform")],
                [button("Set a topic", "menu:topic"),
                 button("Let it decide", "topic:auto")],
                [button("Clear topic history", "topics:reset")],
                [button("Generate", "go")],
            ),
        )

    def site_menu(self, chat_id: str) -> None:
        brands = [b for b in self.brands.all() if b.slug != "example"]
        self.send(
            chat_id, "Which site?",
            rows(*[[button(b.name, f"site:{b.slug}")] for b in brands],
                 [button("Back", "menu:main")]),
        )

    def count_menu(self, chat_id: str) -> None:
        sel = self.selection(chat_id)
        options = VIDEO_COUNTS if sel.kind == "video" else POST_COUNTS
        self.send(
            chat_id,
            "How many?" if sel.kind == "text" else "How many videos?",
            rows([button(str(n), f"count:{n}") for n in options],
                 [button("Back", "menu:main")]),
        )

    def length_menu(self, chat_id: str) -> None:
        self.send(
            chat_id, "How long?",
            rows([button(f"{s}s", f"secs:{s}") for s in VIDEO_LENGTHS],
                 [button("Back", "menu:main")]),
        )

    def platform_menu(self, chat_id: str) -> None:
        sel = self.selection(chat_id)
        if not sel.brand_slug:
            return self.send(chat_id, "Pick a site first.", None)
        brand = self.brands.get(sel.brand_slug)
        pages = self.studio.text_platforms(brand)
        self.send(
            chat_id, "Which page?",
            rows(*[[button(t.label or t.channel, f"plat:{t.channel}")] for t in pages],
                 [button("All text pages", "plat:all")],
                 [button("Back", "menu:main")]),
        )

    def ask_topic(self, chat_id: str) -> None:
        self.selection(chat_id).awaiting_topic = True
        self.send(
            chat_id,
            "Send the topic as a message.\n\n"
            "For example: Weekend offer — 30% off every service, Friday to Sunday.",
            rows([button("Cancel", "menu:main")]),
        )

    # -- input ---------------------------------------------------------

    def on_text(self, chat_id: str, text: str) -> None:
        """A plain message: either a topic we asked for, or a command."""

        sel = self.selection(chat_id)
        if sel.awaiting_topic and text.strip():
            sel.topic = text.strip()
            sel.awaiting_topic = False
            self.send(chat_id, f"Topic set:\n{sel.topic}", None)
            return self.main_menu(chat_id)

        if text.strip().lower() in ("/start", "/menu", "start", "menu"):
            return self.main_menu(chat_id)

        self.send(chat_id, "Use the menu below.", None)
        self.main_menu(chat_id)

    def on_action(self, chat_id: str, action: str) -> None:
        """A button press. One short action; the choices accumulate."""

        sel = self.selection(chat_id)
        name, _, value = action.partition(":")

        if name == "menu":
            return {
                "main": self.main_menu, "site": self.site_menu,
                "count": self.count_menu, "platform": self.platform_menu,
                "topic": self.ask_topic, "length": self.length_menu,
            }.get(value, self.main_menu)(chat_id)

        if name == "site":
            sel.brand_slug = value
            # A different site means the old page choice may not exist on it.
            sel.platform = None
            return self.main_menu(chat_id)

        if name == "kind":
            sel.kind = value
            if value == "video":
                sel.platform = None
                sel.count = min(sel.count, max(VIDEO_COUNTS))
                return self.length_menu(chat_id)
            return self.main_menu(chat_id)

        if name == "count":
            sel.count = int(value)
            return self.main_menu(chat_id)

        if name == "secs":
            sel.seconds = int(value)
            return self.main_menu(chat_id)

        if name == "plat":
            sel.platform = None if value == "all" else value
            return self.main_menu(chat_id)

        if name == "topic" and value == "auto":
            sel.topic = None
            return self.main_menu(chat_id)

        if name == "go":
            return self.generate(chat_id, allow_repeats=(value == "repeat"))

        if name == "topics" and value == "reset":
            freed = self.studio.free_topics(sel.brand_slug) if sel.brand_slug else 0
            self.send(chat_id, f"Cleared {freed} covered topic(s). "
                               f"Everything is available again.", None)
            return self.main_menu(chat_id)

        self.main_menu(chat_id)

    # -- generation ----------------------------------------------------

    def generate(self, chat_id: str, allow_repeats: bool = False) -> None:
        sel = self.selection(chat_id)
        if not sel.brand_slug:
            self.send(chat_id, "Pick a site first.", None)
            return self.site_menu(chat_id)

        brand = self.brands.get(sel.brand_slug)
        self.send(chat_id, f"Working on {sel.summary(brand.name)}…", None)

        try:
            if sel.kind == "video":
                batch = self.studio.make_videos(
                    sel.brand_slug, count=sel.count, seconds=sel.seconds,
                    topic=sel.topic, allow_repeats=allow_repeats,
                )
                for package in batch.videos:
                    for message in package.as_telegram_messages():
                        self.send(chat_id, message, None)
                    for warning in package.warnings():
                        self.send(chat_id, f"heads up: {warning}", None)
            else:
                batch = self.studio.make_posts(
                    sel.brand_slug, count=sel.count, platform=sel.platform,
                    topic=sel.topic, allow_repeats=allow_repeats,
                )
                total = len(batch.posts)
                for i, post in enumerate(batch.posts, 1):
                    for message in post.as_telegram_messages(i, total):
                        self.send(chat_id, message, None)
        except Exception as exc:  # noqa: BLE001
            # A failed generation must leave the menu usable rather than
            # dropping the chat into a dead end with no way back.
            self.send(chat_id, f"That didn't work: {exc}", None)
            return self.main_menu(chat_id)

        if getattr(batch, "unresearched", False):
            self.send(
                chat_id,
                f"{len(batch)} item(s) — but this site has no topics yet. "
                f"Nothing has been read about it and no research has run, so "
                f"these were written from the brand profile alone.\n\n"
                f"Read its website or run research and the next batch will be "
                f"far more specific.",
                rows([button("Menu", "menu:main")]),
            )
            return

        if getattr(batch, "exhausted", False):
            self.send(
                chat_id,
                f"{len(batch)} item(s), but {batch.without_topic} had no fresh "
                f"topic left — this site has been written about on every angle "
                f"the framework knows.\n\nEither research more topics for it, "
                f"or allow repeats.",
                rows([button("Allow repeats once", "go:repeat")],
                     [button("Clear topic history", "topics:reset")],
                     [button("Menu", "menu:main")]),
            )
            return

        self.send(
            chat_id, f"{len(batch)} item(s). Copy what you want.",
            rows([button("More like this", "go"),
                  button("Different topics", "topic:auto")],
                 [button("Menu", "menu:main")]),
        )


class TelegramTransport:
    """Long-polls Telegram and drives a ControlBot.

    Separate from ControlBot so the menu logic can be tested without a
    network, and so the same menus could be driven from a CLI later.
    """

    def __init__(self, token: Optional[str] = None,
                 authorized_chat_ids: Optional[list[str]] = None):
        import os

        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        default = os.environ.get("TELEGRAM_CHAT_ID")
        self.authorized: set[str] = {str(c) for c in (authorized_chat_ids or []) if c}
        if default:
            self.authorized.add(str(default))
        self._offset = 0

    def _api(self, method: str, **params) -> dict:
        import requests

        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/{method}",
            json=params, timeout=40,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram error on {method}: {data}")
        return data["result"]

    def send(self, chat_id: str, text: str, keyboard: Optional[dict] = None) -> None:
        # Telegram rejects messages over 4096 characters, and a long video
        # script or a 10-post batch will exceed that — so split rather than
        # lose the tail of a post.
        for chunk in _split_message(text):
            params = {"chat_id": chat_id, "text": chunk}
            if keyboard and chunk is _split_message(text)[-1]:
                params["reply_markup"] = keyboard
            self._api("sendMessage", **params)

    def run(self, bot: ControlBot, poll_seconds: int = 30) -> None:
        """Poll forever, handing updates to the bot. Ctrl+C to stop."""

        print("Control bot listening. Send /start in Telegram.")
        while True:
            updates = self._api("getUpdates", offset=self._offset, timeout=poll_seconds)
            for update in updates:
                self._offset = update["update_id"] + 1
                self._handle(bot, update)

    def _handle(self, bot: ControlBot, update: dict) -> None:
        callback = update.get("callback_query")
        if callback:
            chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
            if not self._allowed(chat_id):
                return self._refuse(callback["id"])
            self._api("answerCallbackQuery", callback_query_id=callback["id"])
            return bot.on_action(chat_id, callback.get("data", ""))

        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")
        if text and self._allowed(chat_id):
            bot.on_text(chat_id, text)

    def _allowed(self, chat_id: str) -> bool:
        # Fails closed, like the approval bot: an empty allow-list trusts
        # nobody, so a misconfigured deployment ignores everyone rather than
        # handing the studio to whoever finds the bot.
        return bool(chat_id) and chat_id in self.authorized

    def _refuse(self, callback_id: str) -> None:
        self._api("answerCallbackQuery", callback_query_id=callback_id,
                  text="Not authorized", show_alert=True)


#: Telegram's hard limit on a single message.
MAX_MESSAGE_CHARS = 4096


def _split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Break an over-long message on line boundaries where possible."""

    if len(text) <= limit:
        return [text]

    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # a single line longer than the limit
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks
