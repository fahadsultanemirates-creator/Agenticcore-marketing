from agenticcore.performance import (
    MIN_POSTS_FOR_SIGNAL,
    MetricsStore,
    PerformanceMemory,
    PostMetrics,
    extract_metrics,
)
from agenticcore.queue import DraftStore


def test_extracts_metrics_from_a_deeply_nested_payload():
    payload = {"facebook": {"analytics": {"impressions": 1000, "commentCount": 5}}}

    m = extract_metrics(payload, "d1", "acme", "facebook")

    assert m.impressions == 1000
    assert m.comments == 5


def test_sums_a_reaction_breakdown_into_likes():
    """Facebook reports reactions as {like: n, love: n, ...}, not a single count."""

    payload = {"reactions": {"like": 60, "love": 12, "wow": 3}}

    assert extract_metrics(payload, "d", "a", "facebook").likes == 75


def test_prefers_impressions_over_reach_when_both_are_present():
    payload = {"reach": 500, "impressions": 900}

    assert extract_metrics(payload, "d", "a", "facebook").impressions == 900


def test_handles_each_networks_own_vocabulary():
    tiktok = extract_metrics({"videoViews": 8000, "diggCount": 400}, "d", "a", "tiktok")
    youtube = extract_metrics({"viewCount": 1200, "likeCount": 44}, "d", "a", "youtube")
    twitter = extract_metrics({"impressionCount": 300, "retweetCount": 7}, "d", "a", "twitter")

    assert tiktok.impressions == 8000 and tiktok.likes == 400
    assert youtube.impressions == 1200 and youtube.likes == 44
    assert twitter.impressions == 300 and twitter.shares == 7


def test_missing_metrics_are_zero_not_an_error():
    m = extract_metrics({"somethingElse": "text"}, "d", "a", "telegram")

    assert (m.impressions, m.likes, m.engagements) == (0, 0, 0)


def test_booleans_are_not_counted_as_numbers():
    """A flag like {"isVideo": true} must not become 1 impression."""

    assert extract_metrics({"impressions": True}, "d", "a", "x").impressions == 0


def test_engagement_rate_is_zero_when_nothing_was_seen():
    """An unseen post has no rate; inventing one would flatter it."""

    assert PostMetrics("d", "a", "facebook", impressions=0, likes=5).engagement_rate == 0.0


def test_rate_lets_a_small_page_outrank_a_big_one():
    small = PostMetrics("d1", "a", "telegram", impressions=200, likes=40)
    big = PostMetrics("d2", "a", "linkedin", impressions=20_000, likes=100)

    assert small.engagement_rate > big.engagement_rate
    assert big.impressions > small.impressions  # volume says the opposite


def test_store_refreshes_a_post_in_place_as_views_accrue(tmp_path):
    store = MetricsStore(tmp_path / "m.db")
    store.record(PostMetrics("d1", "acme", "facebook", impressions=100))
    store.record(PostMetrics("d1", "acme", "facebook", impressions=450))

    rows = store.for_brand("acme")
    assert len(rows) == 1
    assert rows[0].impressions == 450


def _seeded_memory(tmp_path, count):
    drafts = DraftStore(tmp_path / "db.sqlite")
    metrics = MetricsStore(tmp_path / "db.sqlite")
    for i in range(count):
        d = drafts.create(brand_slug="acme", channel="linkedin", caption=f"Post number {i}")
        drafts.update(d.id, status="published")
        metrics.record(PostMetrics(d.id, "acme", "linkedin",
                                   impressions=1000, likes=i * 10))
    return PerformanceMemory(drafts, metrics)


def test_digest_is_empty_until_there_is_real_signal(tmp_path):
    """Imitating the best of two posts would bake one random result into the voice."""

    memory = _seeded_memory(tmp_path, MIN_POSTS_FOR_SIGNAL - 1)

    assert memory.strategy_digest("acme") == ""


def test_digest_ranks_best_and_worst_with_their_copy(tmp_path):
    memory = _seeded_memory(tmp_path, 6)

    digest = memory.strategy_digest("acme", top_n=2)

    assert "BEST PERFORMING" in digest and "WEAKEST" in digest
    assert "Post number 5" in digest   # highest likes
    assert "Post number 0" in digest   # lowest
    best_at = digest.index("BEST PERFORMING")
    assert digest.index("Post number 5") > best_at
    assert digest.index("Post number 5") < digest.index("WEAKEST")


def test_recent_captions_returns_only_published_posts(tmp_path):
    drafts = DraftStore(tmp_path / "db.sqlite")
    metrics = MetricsStore(tmp_path / "db.sqlite")
    live = drafts.create(brand_slug="acme", channel="linkedin", caption="went out")
    drafts.update(live.id, status="published")
    drafts.create(brand_slug="acme", channel="linkedin", caption="still pending")

    captions = PerformanceMemory(drafts, metrics).recent_captions("acme")

    assert captions == ["went out"]
