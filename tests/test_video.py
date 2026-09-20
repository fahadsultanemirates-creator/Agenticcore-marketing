import pytest

from agenticcore.brands import BrandProfile
from agenticcore.llm import EchoLLMClient
from agenticcore.video import (
    THUMBNAIL_MAX_WORDS,
    YOUTUBE_TITLE_CHARS,
    VideoPackage,
    VideoPackageAgent,
    _shape_for,
    parse_package,
)

REPLY = """SCRIPT: Most brokers pay an agency monthly and cannot say what it bought.
Ask for the line items.
THUMBNAIL: Ask For The Line Items
TITLE: The question most brokers never ask their agency
BRIEF: If you cannot get a breakdown, you are buying a retainer, not marketing.
HASHTAGS: realestate, marketing, brokerage
"""


def test_parses_every_piece_of_the_package():
    p = parse_package(REPLY, "acme", 10, topic="transparency")

    assert p.script.startswith("Most brokers")
    assert p.thumbnail_text == "Ask For The Line Items"
    assert p.hashtags == ["#realestate", "#marketing", "#brokerage"]
    assert p.seconds == 10


def test_title_becomes_the_first_line_of_the_caption():
    """One thing to paste, not two — the title doubles as the caption opener."""

    p = parse_package(REPLY, "acme", 10)

    assert p.brief.startswith("The question most brokers never ask")
    assert p.title_line == "The question most brokers never ask their agency"


def test_title_is_not_duplicated_when_the_model_already_joined_them():
    reply = "SCRIPT: words\nTITLE: A good hook\nBRIEF: A good hook\n\nThen the rest."

    assert parse_package(reply, "a", 10).brief.count("A good hook") == 1


def test_a_title_over_the_youtube_limit_is_flagged():
    """YouTube rejects it outright, so this has to be caught before upload."""

    p = VideoPackage("a", 10, script="x " * 25, brief="T" * 130)

    assert any("YouTube Shorts cuts at" in w for w in p.warnings())
    assert len(p.title_line) > YOUTUBE_TITLE_CHARS


def test_a_thumbnail_that_is_too_long_is_flagged():
    p = VideoPackage("a", 10, script="x " * 25, brief="ok",
                     thumbnail_text="this thumbnail line is far too long to read at a glance")

    assert any("words" in w and "glance" in w for w in p.warnings())
    assert len(p.thumbnail_text.split()) > THUMBNAIL_MAX_WORDS


def test_a_script_that_overruns_its_slot_is_flagged():
    """A 10-second slot with 40 seconds of speech has to be recut."""

    p = VideoPackage("a", 10, script="word " * 100, brief="ok", thumbnail_text="four words here now")

    assert any("will need recutting" in w for w in p.warnings())


def test_running_slightly_short_is_not_flagged():
    """Overrunning means a recut; running short just means holding a beat."""

    p = VideoPackage("a", 10, script="word " * 20, brief="ok", thumbnail_text="four words here now")

    assert p.estimated_seconds < p.seconds
    assert p.warnings() == []


def test_a_badly_under_written_script_is_flagged():
    p = VideoPackage("a", 60, script="word " * 20, brief="ok", thumbnail_text="four words here now")

    assert any("under-written" in w for w in p.warnings())


def test_a_well_formed_package_has_no_warnings():
    p = parse_package(REPLY, "acme", 10)

    assert p.warnings() == []


def test_an_empty_script_is_caught():
    assert "No script came back." in VideoPackage("a", 10, script="   ").warnings()


def test_delivered_as_separate_messages_one_per_paste():
    p = parse_package(REPLY, "acme", 10, topic="transparency")

    messages = p.as_telegram_messages()

    assert len(messages) == 3
    assert "SCRIPT" in messages[0] and "transparency" in messages[0]
    assert "THUMBNAIL TEXT" in messages[1]
    assert "YouTube title" in messages[2]
    assert "#realestate" in messages[2]  # hashtags ride with the caption


def test_ten_and_sixty_seconds_are_different_shapes():
    """Not the same script trimmed — a cut 60s script loses its payoff."""

    assert "ONE idea only" in _shape_for(10)
    assert "three beats" in _shape_for(60)
    assert _shape_for(10) != _shape_for(60)


def test_the_agent_asks_for_a_word_count_matching_the_slot():
    class Recording:
        def __init__(self): self.prompt = ""
        def complete(self, system, prompt):
            self.prompt = prompt
            return REPLY

    brand = BrandProfile.from_dict({
        "slug": "a", "name": "Acme", "audience": "Brokers",
        "forbidden_claims": ["any guaranteed result"],
    })
    llm = Recording()

    VideoPackageAgent(llm).write(brand, 60, topic="pricing", website_brief="Sells websites.")

    assert "60 seconds" in llm.prompt
    assert "150 spoken words" in llm.prompt   # 60 * 2.5
    assert "pricing" in llm.prompt
    assert "Sells websites." in llm.prompt
    assert "any guaranteed result" in llm.prompt
