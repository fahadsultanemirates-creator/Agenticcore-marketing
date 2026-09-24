import re
import json

import pytest

from agenticcore.brands import BrandRegistry
from agenticcore.control import (
    DIVIDER, MAX_MESSAGE_CHARS, ControlBot, Selection, TelegramTransport,
    _split_message,
)
from agenticcore.llm import EchoLLMClient
from agenticcore.orchestrator import MarketingOrchestrator
from agenticcore.queue import DraftStore
from agenticcore.research import WebsiteStore, parse_profile
from agenticcore.topics import TopicLedger
from agenticcore.studio import PostStudio


class ScriptedLLM:
    """Answers the video agent in its format; echoes otherwise."""

    def complete(self, system, prompt):
        if "vertical video" in system:
            return ("SCRIPT: Ask your agency for the line items.\n"
                    "THUMBNAIL: Ask For Line Items\n"
                    "TITLE: The question brokers never ask\n"
                    "BRIEF: If nobody will show you a breakdown, that is the answer.\n"
                    "HASHTAGS: realestate, marketing\n")
        return f"[echo] {prompt[:200]}"


def make_bot(tmp_path, llm=None):
    data = {
        "slug": "acme", "name": "Acme", "audience": "Brokers",
        "goal": "Signups", "media": "none",
        "channels": [
            {"channel": "facebook", "label": "Acme FB"},
            {"channel": "telegram", "label": "Acme Channel"},
            {"channel": "tiktok", "label": "Acme TikTok", "media": "video"},
        ],
    }
    d = tmp_path / "brands"
    d.mkdir(exist_ok=True)
    (d / "acme.json").write_text(json.dumps(data))
    (d / "beta.json").write_text(json.dumps({**data, "slug": "beta", "name": "Beta"}))
    brands = BrandRegistry(d)

    sent = []
    websites = WebsiteStore(tmp_path / "c.db")
    for slug in ("acme", "beta"):
        websites.save(parse_profile(
            "TOPICS: " + " | ".join(f"a good angle number {i}" for i in range(12)),
            slug, f"https://{slug}.test"))
    studio = PostStudio(brands, MarketingOrchestrator(llm=llm or EchoLLMClient()),
                        websites=websites, store=DraftStore(tmp_path / "c.db"),
                        ledger=TopicLedger(tmp_path / "c.db"), critique=False)
    bot = ControlBot(studio, brands,
                     send=lambda chat, text, kb=None: sent.append((chat, text, kb)))
    return bot, sent


def labels(keyboard):
    return [b["text"] for row in (keyboard or {}).get("inline_keyboard", []) for b in row]


def test_the_main_menu_offers_every_choice(tmp_path):
    bot, sent = make_bot(tmp_path)

    bot.main_menu("1")

    buttons = labels(sent[-1][2])
    for expected in ("Switch site", "How many", "Which page",
                     "Set a topic", "Let it decide", "Generate"):
        assert expected in buttons
    # Format is not a question any more — the page answers it.
    assert "Text posts" not in buttons and "Video" not in buttons


def test_switching_site_lists_the_brands(tmp_path):
    bot, sent = make_bot(tmp_path)

    bot.on_action("1", "menu:site")

    assert {"Acme", "Beta"} <= set(labels(sent[-1][2]))


def test_choosing_a_site_is_remembered(tmp_path):
    bot, sent = make_bot(tmp_path)

    bot.on_action("1", "site:beta")

    assert bot.selection("1").brand_slug == "beta"
    # Straight on to picking a page, since a run needs one.
    assert "Which page?" in sent[-1][1]


def test_switching_site_clears_a_page_that_may_not_exist_there(tmp_path):
    bot, _ = make_bot(tmp_path)
    bot.on_action("1", "site:acme")
    bot.on_action("1", "plat:facebook")

    bot.on_action("1", "site:beta")

    assert bot.selection("1").platform is None


def test_count_can_be_three_without_making_ten(tmp_path):
    bot, _ = make_bot(tmp_path)

    bot.on_action("1", "count:3")

    assert bot.selection("1").count == 3


def test_picking_a_video_page_switches_the_run_to_video(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.on_action("1", "site:acme")

    bot.on_action("1", "plat:tiktok")

    assert bot.selection("1").is_video is True
    # The length question appears only once a video page is chosen.
    assert "How long" in labels(sent[-1][2])


def test_picking_a_text_page_hides_the_length_question(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.on_action("1", "site:acme")

    bot.on_action("1", "plat:facebook")

    assert bot.selection("1").is_video is False
    assert "How long" not in labels(sent[-1][2])


def test_the_page_menu_offers_every_page_marked_text_or_video(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.on_action("1", "site:acme")

    bot.on_action("1", "menu:platform")

    buttons = labels(sent[-1][2])
    assert "Acme FB — text" in buttons
    assert "Acme Channel — text" in buttons
    assert "Acme TikTok — video" in buttons
    # One page per run, so there is nothing to generate everywhere at once.
    assert not any("All" in b for b in buttons)


def test_generating_without_a_page_asks_for_one(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.selection("1").brand_slug = "acme"
    sent.clear()

    bot.on_action("1", "go")

    assert "Pick a page first." in sent[0][1]
    assert "Which page?" in sent[-1][1]


def test_a_topic_typed_as_a_message_is_captured(tmp_path):
    bot, _ = make_bot(tmp_path)
    bot.on_action("1", "menu:topic")

    bot.on_text("1", "Weekend offer: 30% off every service")

    assert bot.selection("1").topic == "Weekend offer: 30% off every service"
    assert bot.selection("1").awaiting_topic is False


def test_letting_the_framework_decide_clears_the_topic(tmp_path):
    bot, _ = make_bot(tmp_path)
    bot.selection("1").topic = "an old topic"

    bot.on_action("1", "topic:auto")

    assert bot.selection("1").topic is None


def test_generating_without_a_site_asks_for_one(tmp_path):
    bot, sent = make_bot(tmp_path)

    bot.on_action("1", "go")

    assert "Pick a site first." in sent[0][1]


def test_generating_sends_one_message_per_post(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.on_action("1", "site:acme")
    bot.on_action("1", "plat:facebook")
    bot.on_action("1", "count:3")
    sent.clear()

    bot.on_action("1", "go")

    # Each post is a ruled heading plus the caption on its own, so the
    # caption can be copied without a header to strip off it.
    heads = [t for _, t, _ in sent if t.startswith(DIVIDER)]
    assert [h.splitlines()[1].split(" · ")[0] for h in heads] == [
        "POST 1 of 3", "POST 2 of 3", "POST 3 of 3"]
    assert all("Acme FB" in h for h in heads)
    assert any("Copy what you want" in t for _, t, _ in sent)


def test_a_video_run_sends_the_three_pieces_separately(tmp_path):
    bot, sent = make_bot(tmp_path, llm=ScriptedLLM())
    bot.on_action("1", "site:acme")
    bot.on_action("1", "plat:tiktok")
    bot.on_action("1", "secs:10")
    bot.on_action("1", "count:1")
    sent.clear()

    bot.on_action("1", "go")

    texts = [t for _, t, _ in sent]
    assert any(t.startswith(DIVIDER) and "VIDEO 1 of 1" in t for t in texts)
    # Upload order: what you say, what you paste, what goes on the cover.
    order = [t for t in texts if t[:2] in ("1.", "2.", "3.")]
    assert [o.split(" — ")[0] for o in order] == [
        "1. SCRIPT", "2. DESCRIPTION", "3. THUMBNAIL TEXT"]


def test_two_chats_keep_separate_selections(tmp_path):
    bot, _ = make_bot(tmp_path)

    bot.on_action("1", "site:acme")
    bot.on_action("2", "site:beta")

    assert bot.selection("1").brand_slug == "acme"
    assert bot.selection("2").brand_slug == "beta"


def test_a_failed_generation_leaves_the_menu_usable(tmp_path):
    """A dead end with no way back is worse than an error message."""

    bot, sent = make_bot(tmp_path)
    bot.on_action("1", "site:acme")
    bot.on_action("1", "plat:facebook")
    bot.studio.make_posts = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    sent.clear()

    bot.on_action("1", "go")

    assert any("didn't work" in t for _, t, _ in sent)
    assert labels(sent[-1][2])  # a menu came back


def test_unknown_actions_fall_back_to_the_menu(tmp_path):
    bot, sent = make_bot(tmp_path)

    bot.on_action("1", "something:unexpected")

    assert "AgenticCore studio" in sent[-1][1]


# -- transport -------------------------------------------------------

def test_long_messages_are_split_on_line_boundaries():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(120))

    chunks = _split_message(text)

    assert len(chunks) > 1
    assert all(len(c) <= MAX_MESSAGE_CHARS for c in chunks)
    assert "".join(chunks) == text


def test_a_single_over_long_line_is_still_split():
    chunks = _split_message("y" * (MAX_MESSAGE_CHARS * 2 + 10))

    assert all(len(c) <= MAX_MESSAGE_CHARS for c in chunks)
    assert "".join(chunks) == "y" * (MAX_MESSAGE_CHARS * 2 + 10)


def test_a_short_message_is_not_split():
    assert _split_message("hello") == ["hello"]


def test_the_transport_fails_closed_on_an_empty_allow_list(monkeypatch):
    """Same rule as the approval bot: trust nobody rather than everybody."""

    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    transport = TelegramTransport(token="t")

    assert transport.authorized == set()
    assert transport._allowed("999999") is False


def test_the_transport_trusts_the_configured_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    transport = TelegramTransport(token="t")

    assert transport._allowed("12345") is True
    assert transport._allowed("99999") is False


def test_a_site_with_no_topics_yet_is_told_so_not_told_it_is_exhausted(tmp_path):
    """Never having had topics and having used them all need different fixes."""

    bot, sent = make_bot(tmp_path)
    bot.studio.websites = None          # no site profile at all
    bot.on_action("1", "site:acme")
    bot.on_action("1", "plat:facebook")
    sent.clear()

    bot.on_action("1", "go")

    text = " ".join(t for _, t, _ in sent)
    assert "no topics yet" in text
    assert "exhausted" not in text and "every angle" not in text


def test_an_exhausted_site_offers_repeats_and_a_reset(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.on_action("1", "site:acme")
    bot.on_action("1", "plat:facebook")
    bot.on_action("1", "count:10")
    bot.on_action("1", "go")      # burns 10 of 12
    bot.on_action("1", "go")      # burns the rest and runs dry
    sent.clear()

    bot.on_action("1", "go")

    text = " ".join(t for _, t, _ in sent)
    assert "no fresh topic left" in text
    buttons = labels(sent[-1][2])
    assert "Allow repeats once" in buttons
    assert "Clear topic history" in buttons


def test_clearing_topic_history_frees_everything(tmp_path):
    bot, sent = make_bot(tmp_path)
    bot.on_action("1", "site:acme")
    bot.on_action("1", "count:5")
    bot.on_action("1", "go")
    sent.clear()

    bot.on_action("1", "topics:reset")

    assert "Everything is available again" in sent[0][1]
    assert bot.studio.used_topics("acme") == set()


def test_the_menu_survives_a_message_long_enough_to_split():
    """A long video script must not cost you the buttons."""

    from agenticcore.control import MAX_MESSAGE_CHARS, TelegramTransport

    sent = []

    class Recording(TelegramTransport):
        def __init__(self):
            self.token = "t"
            self.authorized = {"1"}
            self._offset = 0

        def _api(self, method, **params):
            sent.append(params)
            return {}

    keyboard = {"inline_keyboard": [[{"text": "Menu", "callback_data": "menu"}]]}
    Recording().send("1", "x" * (MAX_MESSAGE_CHARS * 2 + 10), keyboard)

    assert len(sent) > 1, "this text should have split"
    assert "reply_markup" not in sent[0]
    assert sent[-1]["reply_markup"] == keyboard


def test_a_short_message_still_carries_its_keyboard():
    from agenticcore.control import TelegramTransport

    sent = []

    class Recording(TelegramTransport):
        def __init__(self):
            self.token = "t"
            self.authorized = {"1"}
            self._offset = 0

        def _api(self, method, **params):
            sent.append(params)
            return {}

    keyboard = {"inline_keyboard": [[{"text": "Menu", "callback_data": "menu"}]]}
    Recording().send("1", "short", keyboard)

    assert len(sent) == 1
    assert sent[0]["reply_markup"] == keyboard
