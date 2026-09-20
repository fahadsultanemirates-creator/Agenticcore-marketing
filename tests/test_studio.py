import json

import pytest

from agenticcore.brands import BrandRegistry
from agenticcore.llm import EchoLLMClient
from agenticcore.orchestrator import MarketingOrchestrator
from agenticcore.queue import DraftStore
from agenticcore.research import (
    OpportunityStore, SignalStore, TerritoryStore, WebsiteStore,
    parse_opportunities, parse_profile, parse_signals, parse_territory,
)
from agenticcore.studio import PostStudio


def write_brand(tmp_path, **overrides):
    data = {
        "slug": "acme", "name": "Acme", "audience": "Brokers",
        "goal": "Drive signups", "media": "none",
        "channels": [
            {"channel": "facebook", "label": "Acme FB"},
            {"channel": "twitter", "label": "Acme X"},
            {"channel": "telegram", "label": "Acme Channel"},
            {"channel": "tiktok", "label": "Acme TikTok", "media": "video"},
        ],
    }
    data.update(overrides)
    d = tmp_path / "brands"
    d.mkdir(exist_ok=True)
    (d / "acme.json").write_text(json.dumps(data))
    return BrandRegistry(d)


def make_studio(tmp_path, critique=False, llm=None, **brand_overrides):
    brands = write_brand(tmp_path, **brand_overrides)
    store = DraftStore(tmp_path / "s.db")
    websites = WebsiteStore(tmp_path / "s.db")
    websites.save(parse_profile(
        "SELLS: Fixed-price websites.\nPRICES: from $150\nPROOF: none stated\n"
        "TOPICS: what $150 buys | why we publish prices | what we refuse to build\n",
        "acme", "https://acme.test"))
    return PostStudio(
        brands, MarketingOrchestrator(llm=llm or EchoLLMClient()),
        websites=websites, store=store, critique=critique,
    ), store, websites


def test_makes_the_number_of_posts_asked_for(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=3)

    assert len(batch.posts) == 3
    assert all(p.caption for p in batch.posts)


def test_three_posts_do_not_need_ten(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    assert len(studio.make_posts("acme", count=1).posts) == 1
    assert len(studio.make_posts("acme", count=10).posts) == 10


def test_a_batch_spreads_across_the_brands_text_pages(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=3)

    # facebook, twitter, telegram — tiktok is a video page and excluded.
    assert {p.platform for p in batch.posts} == {"facebook", "twitter", "telegram"}


def test_one_platform_can_be_requested(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=3, platform="telegram")

    assert {p.platform for p in batch.posts} == {"telegram"}


def test_asking_for_a_video_only_platform_as_text_is_an_error(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    with pytest.raises(ValueError, match="no page for"):
        studio.make_posts("acme", count=1, platform="linkedin")


def test_each_post_gets_its_own_topic_so_they_are_not_rewrites(tmp_path):
    """Five posts on one subject are five rewrites; five subjects are five posts."""

    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=3)

    assert len(set(batch.topics_used)) == 3


def test_a_given_topic_fixes_the_subject_for_every_post(tmp_path):
    """What you want for an announcement, not for browsing ideas."""

    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=3, topic="Weekend offer: 30% off everything")

    assert all(p.topic_source == "given" for p in batch.posts)
    assert all("30% off everything" in p.caption for p in batch.posts)


def test_topics_come_from_the_best_source_available(tmp_path):
    studio, _, websites = make_studio(tmp_path)
    ops = OpportunityStore(tmp_path / "s.db")
    ops.add_all(parse_opportunities("QUERY: a researched question worth answering\n", "acme", []))
    sigs = SignalStore(tmp_path / "s.db")
    sigs.add_all(parse_signals("HEADLINE: something happened this week\nURGENCY: 5\n", "acme", []))
    studio.opportunities, studio.signals = ops, sigs

    picked = studio.choose_topics("acme", 3)

    # A live signal outranks a researched question, which outranks the site's angles.
    assert [source for _, source in picked][:2] == ["signal", "opportunity"]
    assert picked[2][1] == "website"


def test_without_any_source_the_topic_is_left_open_not_invented(tmp_path):
    """A post from the website brief alone is grounded; a fabricated subject isn't."""

    brands = write_brand(tmp_path)
    studio = PostStudio(brands, MarketingOrchestrator(llm=EchoLLMClient()), critique=False)

    assert studio.choose_topics("acme", 2) == [("", "open"), ("", "open")]


def test_the_website_brief_reaches_every_post(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=2)

    assert all("from $150" in p.caption for p in batch.posts)


def test_later_posts_know_what_the_earlier_ones_said(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=3)

    # EchoLLMClient reflects the prompt, so the last caption shows the
    # anti-repetition list it was given.
    assert "already_published" in batch.posts[-1].caption


def test_asking_again_gives_different_material(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    first = studio.make_posts("acme", count=2)
    second = studio.make_posts("acme", count=2)

    assert set(first.topics_used) != set(second.topics_used)


def test_posts_are_saved_as_drafts_not_queued_for_approval(tmp_path):
    """Nothing publishes, so nothing is pending anyone's approval."""

    studio, store, _ = make_studio(tmp_path)

    studio.make_posts("acme", count=2)

    saved = store.list_for_brand("acme")
    assert len(saved) == 2
    assert all(d.status == "draft" for d in saved)


def test_a_link_is_split_out_for_platforms_that_penalise_it(tmp_path):
    class LinksEverything:
        def complete(self, system, prompt):
            return "A post about the thing. https://acme.test/offer"

    studio, _, _ = make_studio(tmp_path, llm=LinksEverything())

    batch = studio.make_posts("acme", count=1, platform="facebook")

    assert "https://acme.test/offer" not in batch.posts[0].caption
    assert batch.posts[0].first_comment == "https://acme.test/offer"


def test_the_disclaimer_is_applied_to_generated_posts(tmp_path):
    studio, _, _ = make_studio(tmp_path, disclaimer="Information only.")

    batch = studio.make_posts("acme", count=1, platform="telegram")

    assert batch.posts[0].caption.endswith("Information only.")


def test_video_batches_get_distinct_topics_too(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    batch = studio.make_videos("acme", count=2, seconds=10)

    assert len(batch.videos) == 2
    assert len(set(batch.topics_used)) == 2
    assert all(v.seconds == 10 for v in batch.videos)


def test_a_post_formats_for_telegram_with_its_context(tmp_path):
    studio, _, _ = make_studio(tmp_path)

    post = studio.make_posts("acme", count=1, platform="twitter").posts[0]
    message = post.as_telegram_message(index=1, total=1)

    assert "[1/1 · twitter]" in message


def test_repeat_requests_keep_moving_through_the_material(tmp_path):
    """"Give me a different batch" must not return the same batch."""

    studio, _, _ = make_studio(tmp_path)

    seen = []
    for _ in range(3):
        seen += studio.make_posts("acme", count=1).topics_used

    assert len(set(seen)) == len(seen)


def test_a_used_topic_is_recorded_on_the_draft(tmp_path):
    studio, store, _ = make_studio(tmp_path)

    batch = studio.make_posts("acme", count=1)

    saved = store.list_for_brand("acme")[0]
    assert saved.topic == batch.posts[0].topic
    assert saved.topic in studio.used_topics("acme") or \
        saved.topic.lower() in {t.lower() for t in studio.used_topics("acme")}


def test_topic_labels_are_readable_not_raw_briefs():
    """This is the line you read when choosing which post to keep."""

    from agenticcore.studio import _headline

    raw = ("Answer the search 'off plan property marketing ideas'. Off-plan is the "
           "dominant sale model in the Gulf; the phrase matches how developers search.")

    label = _headline(raw)

    assert label == "off plan property marketing ideas"
    assert not label.endswith("'")
    assert "dominant sale model" not in label


def test_a_long_label_stops_on_a_word_boundary():
    from agenticcore.studio import _headline

    label = _headline("a " * 80)

    assert label.endswith("…")
    assert "  " not in label.replace("…", "")


def test_an_opportunity_brief_label_drops_its_prefix():
    from agenticcore.studio import _headline

    assert _headline("The question this post must answer: what does it cost\nWhy: x") \
        == "what does it cost"


def test_a_rewritten_post_says_so_next_to_its_score(tmp_path):
    """A 5/10 beside text the critic already fixed reads as a weak final post."""

    class WeakThenFixed:
        def complete(self, system, prompt):
            if "distribution analyst" in system:
                return "SCORE: 4\nPROBLEMS:\n- weak hook\nREVISED:\nA much stronger opening."
            return "a weak first draft"

    studio, _, _ = make_studio(tmp_path, critique=True, llm=WeakThenFixed())

    post = studio.make_posts("acme", count=1, platform="telegram").posts[0]

    assert post.revised is True
    assert post.caption == "A much stronger opening."
    assert "→ rewritten" in post.as_telegram_message()


def test_a_kept_post_shows_its_score_plainly(tmp_path):
    class StrongEnough:
        def complete(self, system, prompt):
            if "distribution analyst" in system:
                return "SCORE: 9\nREVISED:\nnot used"
            return "a strong first draft"

    studio, _, _ = make_studio(tmp_path, critique=True, llm=StrongEnough())

    post = studio.make_posts("acme", count=1, platform="telegram").posts[0]

    assert post.revised is False
    assert "reach 9/10" in post.as_telegram_message()
    assert "rewritten" not in post.as_telegram_message()


def test_the_avoid_list_does_not_grow_with_the_batch(tmp_path):
    """Unbounded, post 10 carries 19 full captions — costly and diluting."""

    from agenticcore.studio import AVOID_CHARS, AVOID_WINDOW

    seen_sizes = []

    class MeasuresItsPrompt:
        def complete(self, system, prompt):
            seen_sizes.append(len(prompt))
            return "A post of perfectly ordinary length that says something."

    studio, _, _ = make_studio(tmp_path, llm=MeasuresItsPrompt())
    studio.make_posts("acme", count=10)

    # The last prompt must not be dramatically larger than the first.
    assert max(seen_sizes) < min(seen_sizes) + AVOID_WINDOW * AVOID_CHARS * 2


def test_only_the_opening_of_a_previous_post_is_carried(tmp_path):
    from agenticcore.studio import AVOID_CHARS, _opening

    long_post = "word " * 500

    assert len(_opening(long_post)) <= AVOID_CHARS + 1
    assert _opening("short one") == "short one"


def test_a_big_batch_finishes_quickly(tmp_path):
    import time

    studio, _, _ = make_studio(tmp_path)

    start = time.time()
    batch = studio.make_posts("acme", count=10)

    assert len(batch.posts) == 10
    assert time.time() - start < 10


def test_videos_never_repeat_a_topic(tmp_path):
    """The hole: video topics were never recorded, so every batch matched."""

    from agenticcore.topics import TopicLedger

    studio, _, _ = make_studio(tmp_path)
    studio.ledger = TopicLedger(tmp_path / "s.db")

    topics = []
    for _ in range(3):
        topics += studio.make_videos("acme", count=1, seconds=10).topics_used

    assert len([t for t in topics if t]) == len({t for t in topics if t})


def test_posts_and_videos_share_one_topic_ledger(tmp_path):
    """A video spends a topic exactly as a post does."""

    from agenticcore.topics import TopicLedger

    studio, _, _ = make_studio(tmp_path)
    studio.ledger = TopicLedger(tmp_path / "s.db")

    post_topics = studio.make_posts("acme", count=1).topics_used
    video_topics = studio.make_videos("acme", count=1, seconds=10).topics_used

    assert set(post_topics) & set(video_topics) == set()


def test_a_named_topic_overrides_the_no_repeat_rule(tmp_path):
    """Asking for it IS the asking."""

    studio, _, _ = make_studio(tmp_path)

    first = studio.make_posts("acme", count=1, topic="Weekend 30% off")
    again = studio.make_posts("acme", count=1, topic="Weekend 30% off")

    assert first.posts[0].topic == again.posts[0].topic == "Weekend 30% off"


def test_allow_repeats_reopens_the_material(tmp_path):
    from agenticcore.topics import TopicLedger

    studio, _, _ = make_studio(tmp_path)
    studio.ledger = TopicLedger(tmp_path / "s.db")
    first = studio.make_posts("acme", count=3).topics_used

    repeated = studio.make_posts("acme", count=3, allow_repeats=True).topics_used

    assert set(repeated) == set(first)


def test_freeing_topics_starts_the_site_over(tmp_path):
    from agenticcore.topics import TopicLedger

    studio, _, _ = make_studio(tmp_path)
    studio.ledger = TopicLedger(tmp_path / "s.db")
    studio.make_posts("acme", count=3)

    studio.free_topics("acme")

    assert studio.ledger.used_keys("acme") == set()
