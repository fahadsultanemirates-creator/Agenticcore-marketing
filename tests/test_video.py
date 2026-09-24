import pytest

from agenticcore.brands import BrandProfile
from agenticcore.llm import EchoLLMClient
from agenticcore.video import (
    length_label,
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

    # Label, content, label, content, label, content: what you paste is
    # never in the same message as the text describing it. The order is
    # upload order — script first, then the caption box, then the cover.
    assert len(messages) == 6
    assert messages[0].startswith("1. SCRIPT")
    assert messages[1] == p.script.strip()          # the script alone
    assert messages[2].startswith("2. DESCRIPTION")
    assert "#realestate" in messages[3]             # hashtags ride with it
    assert "DESCRIPTION" not in messages[3]
    assert messages[4].startswith("3. THUMBNAIL TEXT")
    assert messages[5] == p.thumbnail_text.strip()


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


class TestLongerFormats:
    def test_each_offered_length_gets_its_own_shape(self):
        """A 4-minute script is a different form, not a padded 30-second one."""

        shapes = {s: _shape_for(s) for s in (10, 30, 60, 120, 180, 240)}

        assert len(set(shapes.values())) == 6

    def test_the_longer_shapes_all_carry_a_retention_device(self):
        """Past a minute, something has to earn the next thirty seconds.

        Which device differs by length — a mid-point re-hook at two
        minutes, named stages at three — but a long shape with none of
        them is just a short one padded out.
        """

        devices = ("re-hook", "signpost", "small payoff")
        for seconds in (120, 180, 240):
            shape = _shape_for(seconds).lower()
            assert any(d in shape for d in devices), seconds

    def test_the_word_target_scales_with_the_slot(self):
        class Recording:
            def __init__(self): self.prompt = ""
            def complete(self, system, prompt):
                self.prompt = prompt
                return "SCRIPT: x\nTHUMBNAIL: a b c\nTITLE: t\nBRIEF: b\nHASHTAGS: h"

        llm = Recording()
        brand = BrandProfile.from_dict({"slug": "a", "name": "Acme",
                                        "audience": "Brokers"})
        VideoPackageAgent(llm).write(brand, 240)

        assert "600 spoken words" in llm.prompt

    def test_labels_read_as_minutes_past_sixty_seconds(self):
        assert length_label(10) == "10s"
        assert length_label(60) == "1m"
        assert length_label(240) == "4m"
        assert length_label(90) == "1m 30s"


class TestShortFormLimits:
    def _package(self, seconds, platform):
        return VideoPackage("acme", seconds, script="word " * int(seconds * 2.5),
                            platform=platform)

    def test_four_minutes_on_youtube_is_flagged_as_not_a_short(self):
        warnings = " ".join(self._package(240, "youtube").warnings())

        assert "short-form limit" in warnings and "3m" in warnings

    def test_three_minutes_on_youtube_is_still_a_short(self):
        assert not any("short-form" in w
                       for w in self._package(180, "youtube").warnings())

    def test_tiktok_tolerates_four_minutes_without_complaint(self):
        assert not any("short-form" in w
                       for w in self._package(240, "tiktok").warnings())

    def test_a_package_with_no_named_page_is_not_flagged(self):
        assert not any("short-form" in w
                       for w in self._package(240, "").warnings())
