"""Menu-driven Telegram control for the post studio.

The approval bot answered one question — yes or no on a finished post. This
one drives the studio: which site, which page, how many, what about. The
difference in shape is why it is a separate module rather than more
callbacks bolted onto the approval bot.

A run targets exactly one page. Generating across every text page at once
produced posts for pages you had no intention of posting to that day, read
interleaved, and the page also settles whether the run is text or video —
so picking it first removes a question rather than adding one.

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
POST_COUNTS = (1, 2, 3, 5)
VIDEO_COUNTS = (1, 2, 3)
VIDEO_LENGTHS = (10, 30, 60)

#: Drawn above each item so a batch reads as separate posts rather than one
#: wall. Telegram stacks consecutive messages from the same sender with
#: almost no gap, so separate messages alone do not look separate.
DIVIDER = "━━━━━━━━━━━━━━━"

#: How long Telegram holds an empty long-poll open. Also the worst-case
#: delay before Ctrl+C is noticed, which is what sets it this low.
POLL_SECONDS = 5



@dataclass
class Selection:
    """What a chat has chosen so far.

    One page per run, always. Spreading a batch across every text page at
    once meant generating posts for pages you had no intention of posting
    to that day, and reading them interleaved. Choosing the page first also
    settles whether the run is text or video — a page takes one or the
    other — so there is no separate format question to get wrong.
    """

    brand_slug: Optional[str] = None
    count: int = 3
    seconds: int = 30
    platform: Optional[str] = None
    is_video: bool = False
    topic: Optional[str] = None
    awaiting_topic: bool = False

    def summary(self, brand_name: str = "", page_label: str = "") -> str:
        parts = [brand_name or self.brand_slug or "no site",
                 page_label or self.platform or "no page"]
        if self.is_video:
            parts.append(f"{self.count} × {self.seconds}s video")
        else:
            parts.append(f"{self.count} post(s)")
        parts.append(f"topic: {self.topic}" if self.topic else "topic: framework decides")
        return " · ".join(parts)


def _heading(*parts: str, topic: str = "") -> str:
    """A ruled header so one item is visibly not the next.

    Consecutive messages from the same sender stack in Telegram with
    almost no gap, so splitting a post off its label is not enough on its
    own — the batch still reads as one continuous wall. A rule above each
    item is what actually separates them on a phone.
    """

    line = " · ".join(p for p in parts if p)
    head = f"{DIVIDER}\n{line}"
    return f"{head}\n{topic}" if topic else head


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

    def page_label(self, sel: Selection) -> str:
        """The chosen page's own label, as the brand config names it."""

        if not (sel.brand_slug and sel.platform):
            return ""
        brand = self.brands.get(sel.brand_slug)
        for target in brand.channels:
            if target.channel == sel.platform:
                return target.label or target.channel
        return sel.platform

    def main_menu(self, chat_id: str) -> None:
        sel = self.selection(chat_id)
        brand = self.brands.get(sel.brand_slug) if sel.brand_slug else None
        name = brand.name if brand else ""

        # The length question only exists for a video page, and showing it
        # on a text page invites setting something that does nothing.
        second_row = [button("How many", "menu:count")]
        if sel.is_video:
            second_row.append(button("How long", "menu:length"))

        self.send(
            chat_id,
            f"AgenticCore studio\n\n{sel.summary(name, self.page_label(sel))}",
            rows(
                [button("Switch site", "menu:site"),
                 button("Which page", "menu:platform")],
                second_row,
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
        options = VIDEO_COUNTS if sel.is_video else POST_COUNTS
        self.send(
            chat_id,
            "How many videos?" if sel.is_video else "How many?",
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
        """Every page the brand has, text and video together.

        There is deliberately no "all pages" option. One page per run is
        the whole point: you post to one page at a time, so you generate
        for one page at a time.
        """

        sel = self.selection(chat_id)
        if not sel.brand_slug:
            self.send(chat_id, "Pick a site first.", None)
            return self.site_menu(chat_id)
        brand = self.brands.get(sel.brand_slug)
        buttons = []
        for target in brand.channels:
            video = self.studio.is_video_page(brand, target.channel)
            mark = "video" if video else "text"
            name = target.label or target.channel
            buttons.append([button(f"{name} — {mark}", f"plat:{target.channel}")])
        self.send(
            chat_id, "Which page?",
            rows(*buttons, [button("Back", "menu:main")]),
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
            # A different site means the old page choice may not exist on
            # it, so go straight to picking one rather than leaving the
            # selection pointing at a page this brand does not have.
            sel.platform = None
            sel.is_video = False
            return self.platform_menu(chat_id)

        if name == "count":
            sel.count = int(value)
            return self.main_menu(chat_id)

        if name == "secs":
            sel.seconds = int(value)
            return self.main_menu(chat_id)

        if name == "plat":
            sel.platform = value
            brand = self.brands.get(sel.brand_slug) if sel.brand_slug else None
            sel.is_video = bool(brand) and self.studio.is_video_page(brand, value)
            if sel.is_video:
                # Three text posts is a sensible default; three videos is
                # not — each is a separate shoot.
                sel.count = min(sel.count, max(VIDEO_COUNTS))
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
        if not sel.platform:
            self.send(chat_id, "Pick a page first.", None)
            return self.platform_menu(chat_id)

        brand = self.brands.get(sel.brand_slug)
        label = self.page_label(sel)
        self.send(chat_id, f"Working on {sel.summary(brand.name, label)}…", None)

        try:
            if sel.is_video:
                batch = self.studio.make_videos(
                    sel.brand_slug, count=sel.count, seconds=sel.seconds,
                    topic=sel.topic, allow_repeats=allow_repeats,
                    platform=sel.platform,
                )
                total = len(batch.videos)
                for i, package in enumerate(batch.videos, 1):
                    self.send(chat_id, _heading(
                        f"VIDEO {i} of {total}", label,
                        f"{package.seconds}s", topic=package.topic), None)
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
                    self.send(chat_id, _heading(
                        f"POST {i} of {total}", label,
                        post.score_line(), topic=post.topic), None)
                    self.send(chat_id, post.caption.strip(), None)
                    for extra in post.extra_messages():
                        self.send(chat_id, extra, None)
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
            json=params, timeout=POLL_SECONDS + 10,
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
        chunks = _split_message(text)
        last = len(chunks) - 1
        for i, chunk in enumerate(chunks):
            params = {"chat_id": chat_id, "text": chunk}
            # The buttons ride on the final chunk. Comparing chunks by
            # position, not identity: an earlier version re-split the text
            # and tested `chunk is chunks[-1]`, which holds only while the
            # message is short enough not to split at all. Past 4096
            # characters the two calls returned different string objects,
            # the test never passed, and the menu vanished — leaving no way
            # back except /start.
            if keyboard and i == last:
                params["reply_markup"] = keyboard
            self._api("sendMessage", **params)

    def run(self, bot: ControlBot, poll_seconds: int = POLL_SECONDS) -> None:
        """Poll until interrupted, handing updates to the bot.

        ``poll_seconds`` is how long Telegram holds an empty poll open. It
        doubles as how long Ctrl+C can take to land: a blocking socket read
        is not interruptible on Windows, so the keypress waits for the poll
        to return before Python sees it. Short enough to feel responsive,
        long enough not to hammer the API.
        """

        print(f"Control bot listening. Send /start in Telegram. "
              f"Ctrl+C to stop (up to {poll_seconds}s).")
        import requests

        try:
            while True:
                try:
                    updates = self._api("getUpdates", offset=self._offset,
                                        timeout=poll_seconds)
                except requests.exceptions.RequestException as exc:
                    # A dropped connection is not a reason to stop listening.
                    # This runs unattended on a VPS, and a bot that dies on
                    # the first blip is one you find dead hours later with no
                    # idea when it went.
                    print(f"network hiccup ({exc.__class__.__name__}), retrying")
                    continue
                for update in updates:
                    self._offset = update["update_id"] + 1
                    self._handle(bot, update)
        except KeyboardInterrupt:
            # A stack trace on a deliberate Ctrl+C reads like a crash.
            print("\nStopped.")

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
