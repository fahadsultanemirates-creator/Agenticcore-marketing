from agenticcore.brands import BrandRegistry
from agenticcore.creative.base import OfflineImageGenerator
from agenticcore.llm import EchoLLMClient
from agenticcore.orchestrator import MarketingOrchestrator
from agenticcore.pipeline import ContentPipeline
from agenticcore.queue import DraftStore


class FakeTelegramBot:
    """Records what would be sent instead of calling the real Telegram API."""

    def __init__(self):
        self.sent = []
        self.authorized_chats = set()
        self._next_id = 1
        self._queued_decisions = []

    def add_authorized_chat(self, chat_id):
        if chat_id:
            self.authorized_chats.add(chat_id)

    def send_for_approval(self, draft, caption, chat_id=None):
        self.sent.append((draft.id, caption))
        message_id = self._next_id
        self._next_id += 1
        return message_id

    def queue_decision(self, draft_id, action):
        self._queued_decisions.append((draft_id, action))

    def poll_decisions(self, timeout=30):
        decisions, self._queued_decisions = self._queued_decisions, []
        yield from decisions


class FakeAyrshareClient:
    """Records publish calls instead of hitting the real Ayrshare API."""

    def __init__(self):
        self.calls = []
        self.comments = []

    def publish(self, caption, platforms, media_urls=None, profile_key=None):
        self.calls.append(
            {"caption": caption, "platforms": platforms, "media_urls": media_urls, "profile_key": profile_key}
        )
        return {"status": "success", "id": f"fake-post-{len(self.calls)}"}

    def comment(self, post_id, comment, platforms, profile_key=None):
        self.comments.append(
            {"post_id": post_id, "comment": comment, "platforms": platforms}
        )
        return {"status": "success"}


class FakeVideoGenerator:
    """Records the script it was handed instead of calling HeyGen."""

    def __init__(self):
        self.scripts = []

    def generate_video(self, script):
        self.scripts.append(script)
        return f"https://fake.heygen/{len(self.scripts)}.mp4"


def write_brand(tmp_path, **overrides):
    import json

    data = {
        "slug": "acme",
        "name": "Acme Widgets",
        "audience": "Developers",
        "goal": "Drive signups",
        "ayrshare_profile_key": "profile-123",
        "channels": [
            {"channel": "facebook", "label": "Acme FB Page"},
            {"channel": "instagram", "label": "Acme Instagram"},
        ],
    }
    data.update(overrides)
    brands_dir = tmp_path / "brands"
    brands_dir.mkdir(exist_ok=True)
    (brands_dir / "acme.json").write_text(json.dumps(data))
    return BrandRegistry(brands_dir)


def make_pipeline(tmp_path, video_generator=None, **brand_overrides):
    brands = write_brand(tmp_path, **brand_overrides)
    store = DraftStore(tmp_path / "drafts.db")
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient())
    bot = FakeTelegramBot()
    publisher = FakeAyrshareClient()
    pipeline = ContentPipeline(
        brands, store, orchestrator, bot, publisher,
        OfflineImageGenerator(), video_generator,
    )
    return pipeline, bot, publisher, store


def test_queue_campaign_creates_one_draft_per_channel_and_sends_to_telegram(tmp_path):
    pipeline, bot, _, _ = make_pipeline(tmp_path)

    drafts = pipeline.queue_campaign("acme")

    assert {d.channel for d in drafts} == {"facebook", "instagram"}
    assert all(d.status == "pending_approval" for d in drafts)
    assert len(bot.sent) == 2


def test_approving_a_draft_publishes_only_that_channel(tmp_path):
    pipeline, bot, publisher, store = make_pipeline(tmp_path)
    drafts = pipeline.queue_campaign("acme")
    facebook_draft = next(d for d in drafts if d.channel == "facebook")

    bot.queue_decision(facebook_draft.id, "approve")
    handled = pipeline.process_decisions()

    assert len(handled) == 1
    assert handled[0].status == "published"
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["platforms"] == ["facebook"]
    assert publisher.calls[0]["profile_key"] == "profile-123"

    reloaded = store.get(facebook_draft.id)
    assert reloaded.status == "published"
    assert reloaded.published_post_id == "fake-post-1"


def test_rejecting_a_draft_never_publishes(tmp_path):
    pipeline, bot, publisher, store = make_pipeline(tmp_path)
    drafts = pipeline.queue_campaign("acme")

    bot.queue_decision(drafts[0].id, "reject")
    handled = pipeline.process_decisions()

    assert handled[0].status == "rejected"
    assert publisher.calls == []
    assert store.get(drafts[0].id).status == "rejected"


def test_each_channel_gets_its_own_caption_naming_that_platform(tmp_path):
    """A Facebook page must not receive the Instagram draft, or all of them."""

    pipeline, _, _, _ = make_pipeline(tmp_path)

    drafts = pipeline.queue_campaign("acme")
    by_channel = {d.channel: d.caption for d in drafts}

    assert by_channel["facebook"] != by_channel["instagram"]
    assert "facebook" in by_channel["facebook"]
    assert "instagram" in by_channel["instagram"]
    # The caption published to one page never mentions another page's platform.
    assert "instagram" not in by_channel["facebook"]
    assert "facebook" not in by_channel["instagram"]


def test_video_brand_sets_video_path_and_no_image(tmp_path):
    video = FakeVideoGenerator()
    pipeline, _, _, _ = make_pipeline(tmp_path, video_generator=video, media="video")

    drafts = pipeline.queue_campaign("acme")

    assert len(video.scripts) == 1, "one video is shared across the brand's pages"
    assert all(d.video_path == "https://fake.heygen/1.mp4" for d in drafts)
    assert all(d.image_path is None for d in drafts)


def test_video_brand_falls_back_to_text_when_heygen_is_unconfigured(tmp_path):
    """A missing HEYGEN_API_KEY should cost the video, not the campaign."""

    pipeline, _, _, _ = make_pipeline(tmp_path, video_generator=None, media="video")

    drafts = pipeline.queue_campaign("acme")

    assert drafts and all(d.caption for d in drafts)
    assert all(d.video_path is None and d.image_path is None for d in drafts)


def test_approved_video_draft_publishes_the_video_as_media(tmp_path):
    video = FakeVideoGenerator()
    pipeline, bot, publisher, _ = make_pipeline(tmp_path, video_generator=video, media="video")
    drafts = pipeline.queue_campaign("acme")

    bot.queue_decision(drafts[0].id, "approve")
    pipeline.process_decisions()

    assert publisher.calls[0]["media_urls"] == ["https://fake.heygen/1.mp4"]


def test_media_none_produces_text_only_posts(tmp_path):
    # Facebook and LinkedIn both accept a text-only post; Instagram does not,
    # which is why the default fixture's pages can't be reused here.
    pipeline, _, _, _ = make_pipeline(
        tmp_path,
        media="none",
        channels=[
            {"channel": "facebook", "label": "Acme FB Page"},
            {"channel": "linkedin", "label": "Acme LinkedIn"},
        ],
    )

    drafts = pipeline.queue_campaign("acme")

    assert all(d.image_path is None and d.video_path is None for d in drafts)


def test_pending_drafts_survive_a_restart_and_stay_approvable(tmp_path):
    """--listen resumes an earlier run's drafts instead of generating new ones."""

    pipeline, bot, publisher, store = make_pipeline(tmp_path)
    drafts = pipeline.queue_campaign("acme")

    # A second process over the same store generates nothing of its own.
    resumed = ContentPipeline(
        pipeline.brands, DraftStore(tmp_path / "drafts.db"),
        pipeline.orchestrator, bot, publisher,
    )
    resumed.trust_configured_chats()

    assert {d.id for d in resumed.pending_drafts()} == {d.id for d in drafts}

    bot.queue_decision(drafts[0].id, "approve")
    handled = resumed.process_decisions()
    assert [d.status for d in handled] == ["published"]


def test_queue_all_covers_every_brand(tmp_path):
    import json

    pipeline, _, _, _ = make_pipeline(tmp_path)
    second = {
        "slug": "beta", "name": "Beta Co", "audience": "Designers",
        "channels": [{"channel": "linkedin", "label": "Beta LinkedIn"}],
    }
    (tmp_path / "brands" / "beta.json").write_text(json.dumps(second))
    pipeline.brands.reload()

    drafts = pipeline.queue_all()

    assert {d.brand_slug for d in drafts} == {"acme", "beta"}
    assert len(drafts) == 3  # acme has two pages, beta one


def test_trust_configured_chats_authorizes_every_brand(tmp_path):
    pipeline, bot, _, _ = make_pipeline(tmp_path, telegram_chat_id="-100777")

    pipeline.trust_configured_chats()

    assert "-100777" in bot.authorized_chats


def test_mixed_brand_generates_one_asset_of_each_kind_and_routes_them(tmp_path):
    """A brand spanning YouTube and LinkedIn: one video, one graphic, right pages."""

    video = FakeVideoGenerator()
    pipeline, _, _, _ = make_pipeline(
        tmp_path,
        video_generator=video,
        media="image",  # brand default
        channels=[
            {"channel": "linkedin", "label": "Acme LinkedIn"},
            {"channel": "facebook", "label": "Acme FB Page"},
            {"channel": "youtube", "label": "Acme YouTube", "media": "video"},
            {"channel": "tiktok", "label": "Acme TikTok", "media": "video"},
        ],
    )

    drafts = pipeline.queue_campaign("acme")
    by_channel = {d.channel: d for d in drafts}

    # One video for the two video pages, not one each.
    assert len(video.scripts) == 1
    assert by_channel["youtube"].video_path == by_channel["tiktok"].video_path
    assert by_channel["youtube"].image_path is None

    # The image pages get the graphic and no video.
    assert by_channel["linkedin"].image_path is not None
    assert by_channel["linkedin"].video_path is None
    assert by_channel["linkedin"].image_path == by_channel["facebook"].image_path


def test_per_page_media_overrides_the_brand_default(tmp_path):
    video = FakeVideoGenerator()
    pipeline, _, _, _ = make_pipeline(
        tmp_path,
        video_generator=video,
        media="video",  # brand default is video
        channels=[
            {"channel": "linkedin", "label": "LI", "media": "image"},  # this page opts out
            {"channel": "tiktok", "label": "TT"},                      # this one inherits
        ],
    )

    drafts = {d.channel: d for d in pipeline.queue_campaign("acme")}

    assert drafts["linkedin"].image_path is not None and drafts["linkedin"].video_path is None
    assert drafts["tiktok"].video_path is not None and drafts["tiktok"].image_path is None


def test_video_page_degrades_to_text_when_heygen_is_unconfigured(tmp_path):
    """No HeyGen shouldn't stop the image pages from going out."""

    pipeline, _, _, _ = make_pipeline(
        tmp_path,
        video_generator=None,
        media="image",
        channels=[
            {"channel": "linkedin", "label": "LI"},
            {"channel": "tiktok", "label": "TT", "media": "video"},
        ],
    )

    drafts = {d.channel: d for d in pipeline.queue_campaign("acme")}

    assert drafts["tiktok"].video_path is None and drafts["tiktok"].caption
    assert drafts["linkedin"].image_path is not None  # unaffected


class FakeAnalytics:
    """Returns canned analytics, and can fail for one post on purpose."""

    def __init__(self, payload_by_post=None, fail_for=()):
        self.payload_by_post = payload_by_post or {}
        self.fail_for = set(fail_for)
        self.calls = []

    def post_analytics(self, post_id, platforms=None, profile_key=None):
        self.calls.append((post_id, tuple(platforms or ()), profile_key))
        if post_id in self.fail_for:
            raise RuntimeError("upstream analytics unavailable")
        return self.payload_by_post.get(post_id, {"impressions": 100, "likeCount": 5})


def _pipeline_with_memory(tmp_path, analytics=None, **brand_overrides):
    from agenticcore.performance import MetricsStore, PerformanceMemory

    brands = write_brand(tmp_path, **brand_overrides)
    store = DraftStore(tmp_path / "drafts.db")
    metrics = MetricsStore(tmp_path / "drafts.db")
    memory = PerformanceMemory(store, metrics)
    bot = FakeTelegramBot()
    publisher = FakeAyrshareClient()
    pipeline = ContentPipeline(
        brands, store, MarketingOrchestrator(llm=EchoLLMClient()), bot, publisher,
        OfflineImageGenerator(), None, memory, analytics,
    )
    return pipeline, bot, publisher, memory


def test_collect_metrics_records_results_against_the_right_draft(tmp_path):
    analytics = FakeAnalytics()
    pipeline, bot, _, memory = _pipeline_with_memory(tmp_path, analytics)
    drafts = pipeline.queue_campaign("acme")
    bot.queue_decision(drafts[0].id, "approve")
    pipeline.process_decisions()

    collected = pipeline.collect_metrics("acme")

    assert len(collected) == 1  # only the published one
    assert collected[0].draft_id == drafts[0].id
    assert collected[0].impressions == 100
    # The brand's Profile-Key and the draft's own platform are passed through.
    assert analytics.calls[0][1] == (drafts[0].channel,)
    assert analytics.calls[0][2] == "profile-123"


def test_one_failing_post_does_not_stop_the_sweep(tmp_path):
    pipeline, bot, _, _ = _pipeline_with_memory(tmp_path, FakeAnalytics())
    drafts = pipeline.queue_campaign("acme")
    for d in drafts:
        bot.queue_decision(d.id, "approve")
    pipeline.process_decisions()

    published = [d for d in pipeline.store.list_for_brand("acme", status="published")]
    doomed = published[0].published_post_id
    pipeline.analytics = FakeAnalytics(fail_for=[doomed])

    collected = pipeline.collect_metrics("acme")

    assert len(collected) == len(published) - 1  # the rest still came through


def test_results_reach_the_next_campaigns_strategy_prompt(tmp_path):
    """The loop closing: measured posts show up in the next brief."""

    from agenticcore.performance import MetricsStore, PostMetrics

    pipeline, _, _, memory = _pipeline_with_memory(tmp_path, FakeAnalytics())
    seeded = []
    for i in range(6):
        d = pipeline.store.create(brand_slug="acme", channel="linkedin",
                                  caption=f"Earlier post {i}")
        pipeline.store.update(d.id, status="published")
        memory.metrics.record(PostMetrics(d.id, "acme", "linkedin",
                                          impressions=1000, likes=i * 25))
        seeded.append(d)

    drafts = pipeline.queue_campaign("acme")

    # EchoLLMClient echoes the prompt, so the captions prove what was sent.
    caption = drafts[0].caption
    assert "BEST PERFORMING" in caption
    assert "Earlier post 5" in caption          # the winner was cited
    assert "already_published" in caption       # anti-repetition list was sent


def test_no_history_means_no_performance_section(tmp_path):
    """Day one still works: nothing to learn from yet, nothing injected."""

    pipeline, _, _, _ = _pipeline_with_memory(tmp_path, FakeAnalytics())

    drafts = pipeline.queue_campaign("acme")

    assert "BEST PERFORMING" not in drafts[0].caption
    assert drafts[0].caption


class ScriptedCriticLLM:
    """Echoes like EchoLLMClient, but answers the reach critic in its format."""

    def __init__(self, score=4, revised="A far stronger hook line."):
        self.score, self.revised = score, revised
        self.critique_count = 0

    def complete(self, system, prompt):
        if "distribution analyst" in system:
            self.critique_count += 1
            return f"SCORE: {self.score}\nPROBLEMS:\n- weak hook\nREVISED:\n{self.revised}"
        return f"[draft] {prompt[:120]} https://example.dev/landing"


def _reach_pipeline(tmp_path, llm, critique=True, **brand_overrides):
    brands = write_brand(tmp_path, **brand_overrides)
    store = DraftStore(tmp_path / "drafts.db")
    bot, publisher = FakeTelegramBot(), FakeAyrshareClient()
    pipeline = ContentPipeline(
        brands, store, MarketingOrchestrator(llm=llm), bot, publisher,
        OfflineImageGenerator(), None, None, None, critique,
    )
    return pipeline, bot, publisher, store


def test_a_weak_draft_is_replaced_by_the_critics_rewrite(tmp_path):
    llm = ScriptedCriticLLM(score=4, revised="A far stronger hook line.")
    pipeline, _, _, _ = _reach_pipeline(tmp_path, llm)

    drafts = pipeline.queue_campaign("acme")

    assert all(d.caption == "A far stronger hook line." for d in drafts)
    assert all(d.reach_score == 4 for d in drafts)
    assert llm.critique_count == len(drafts)  # every post is critiqued


def test_a_strong_draft_is_kept_and_only_scored(tmp_path):
    llm = ScriptedCriticLLM(score=9, revised="Should not be used.")
    pipeline, _, _, _ = _reach_pipeline(tmp_path, llm)

    drafts = pipeline.queue_campaign("acme")

    assert all("Should not be used." not in d.caption for d in drafts)
    assert all(d.reach_score == 9 for d in drafts)


def test_critique_can_be_turned_off(tmp_path):
    llm = ScriptedCriticLLM()
    pipeline, _, _, _ = _reach_pipeline(tmp_path, llm, critique=False)

    drafts = pipeline.queue_campaign("acme")

    assert llm.critique_count == 0
    assert all(d.reach_score is None for d in drafts)


def test_link_is_pulled_from_the_body_and_posted_as_a_first_comment(tmp_path):
    """The whole point of the split: a link in a LinkedIn body costs ~60% reach."""

    llm = ScriptedCriticLLM(score=9)
    pipeline, bot, publisher, _ = _reach_pipeline(
        tmp_path, llm, media="none",
        channels=[{"channel": "linkedin", "label": "Acme LinkedIn"}],
    )

    drafts = pipeline.queue_campaign("acme")
    draft = drafts[0]
    assert "https://example.dev/landing" not in draft.caption
    assert draft.first_comment == "https://example.dev/landing"

    bot.queue_decision(draft.id, "approve")
    pipeline.process_decisions()

    assert publisher.comments == [{
        "post_id": "fake-post-1",
        "comment": "https://example.dev/landing",
        "platforms": ["linkedin"],
    }]


def test_a_failed_first_comment_does_not_unpublish_the_post(tmp_path):
    llm = ScriptedCriticLLM(score=9)
    pipeline, bot, publisher, store = _reach_pipeline(
        tmp_path, llm, media="none",
        channels=[{"channel": "linkedin", "label": "Acme LinkedIn"}],
    )
    drafts = pipeline.queue_campaign("acme")

    def boom(**kwargs):
        raise RuntimeError("comments endpoint unavailable")
    publisher.comment = boom

    bot.queue_decision(drafts[0].id, "approve")
    handled = pipeline.process_decisions()

    assert handled[0].status == "published"
    assert store.get(drafts[0].id).published_post_id == "fake-post-1"


def test_telegram_keeps_its_link_in_the_body(tmp_path):
    llm = ScriptedCriticLLM(score=9)
    pipeline, _, _, _ = _reach_pipeline(
        tmp_path, llm, media="none",
        channels=[{"channel": "telegram", "label": "Acme Channel"}],
    )

    draft = pipeline.queue_campaign("acme")[0]

    assert "https://example.dev/landing" in draft.caption
    assert draft.first_comment is None


def test_the_brands_search_keyword_reaches_the_writer(tmp_path):
    """Search reach compounds, so the keyword has to be in the copy."""

    from agenticcore.llm import EchoLLMClient

    pipeline, _, _, _ = _reach_pipeline(
        tmp_path, EchoLLMClient(), critique=False,
        keywords=["ai marketing automation"],
        media="none",
        channels=[{"channel": "linkedin", "label": "LI"}],
    )

    draft = pipeline.queue_campaign("acme")[0]

    assert "ai marketing automation" in draft.caption  # echoed back from the prompt


def _pipeline_with_opportunities(tmp_path, blocks, **brand_overrides):
    from agenticcore.research import OpportunityStore, parse_opportunities

    # Text-capable pages: Instagram can't take a text-only post, so the
    # default fixture's page set is rejected by the brand media guard.
    brand_overrides.setdefault("channels", [
        {"channel": "linkedin", "label": "Acme LinkedIn"},
        {"channel": "facebook", "label": "Acme FB Page"},
    ])
    brands = write_brand(tmp_path, **brand_overrides)
    store = DraftStore(tmp_path / "drafts.db")
    ops = OpportunityStore(tmp_path / "drafts.db")
    ops.add_all(parse_opportunities(blocks, "acme", ["https://src.test"]))
    bot, publisher = FakeTelegramBot(), FakeAyrshareClient()
    pipeline = ContentPipeline(
        brands, store, MarketingOrchestrator(llm=EchoLLMClient()), bot, publisher,
        OfflineImageGenerator(), None, None, None, False, ops,
    )
    return pipeline, ops


RESEARCHED = """### OPPORTUNITY
QUERY: how much does an AI automation agency actually cost
EVIDENCE: Asked constantly; every agency site says contact us.
ANGLE: Publish real ranges.
FORMAT: post

### OPPORTUNITY
QUERY: who owns the code when the project ends
EVIDENCE: Top vetting question in buyer guides.
ANGLE: State terms before being asked.
FORMAT: post
"""


def test_the_campaign_is_written_about_the_researched_question(tmp_path):
    pipeline, _ = _pipeline_with_opportunities(tmp_path, RESEARCHED, media="none")

    drafts = pipeline.queue_campaign("acme")

    # EchoLLMClient reflects the prompt, so the caption proves what was sent.
    assert "how much does an AI automation agency actually cost" in drafts[0].caption
    assert "do not embellish" in drafts[0].caption


def test_using_an_opportunity_spends_it_so_the_next_campaign_moves_on(tmp_path):
    from agenticcore.research import STATUS_USED

    pipeline, ops = _pipeline_with_opportunities(tmp_path, RESEARCHED, media="none")

    first = pipeline.queue_campaign("acme")
    second = pipeline.queue_campaign("acme")

    assert "how much does an AI automation agency" in first[0].caption
    assert "who owns the code" in second[0].caption
    used = ops.for_brand("acme", status=STATUS_USED)
    assert len(used) == 2
    assert used[0].used_by_draft == first[0].id


def test_an_empty_queue_still_produces_a_campaign(tmp_path):
    """Day one, or a spent territory: fall back rather than fail."""

    pipeline, _ = _pipeline_with_opportunities(tmp_path, "", media="none")

    drafts = pipeline.queue_campaign("acme")

    assert drafts and all(d.caption for d in drafts)


def test_without_an_opportunity_store_nothing_changes(tmp_path):
    pipeline, _, _, _ = make_pipeline(tmp_path)

    drafts = pipeline.queue_campaign("acme")

    assert drafts and all(d.caption for d in drafts)


def test_each_post_targets_the_next_gap_in_the_territory(tmp_path):
    """Instead of repeating one static brand keyword on every post forever."""

    from agenticcore.research import TerritoryStore, parse_territory

    brands = write_brand(tmp_path, media="none", keywords=["static brand keyword"],
                         channels=[{"channel": "linkedin", "label": "LI"}])
    store = DraftStore(tmp_path / "d.db")
    territory = TerritoryStore(tmp_path / "d.db")
    territory.add_all(parse_territory(
        "### TERM\nTERM: how much does an ai agency cost\nPRIORITY: 5\n"
        "### TERM\nTERM: ai agent vs zapier\nPRIORITY: 4\n", "acme", []))

    pipeline = ContentPipeline(
        brands, store, MarketingOrchestrator(llm=EchoLLMClient()),
        FakeTelegramBot(), FakeAyrshareClient(), OfflineImageGenerator(),
        None, None, None, False, None, territory,
    )

    first = pipeline.queue_campaign("acme")
    second = pipeline.queue_campaign("acme")

    # EchoLLMClient reflects the prompt, so the caption shows the keyword used.
    assert "how much does an ai agency cost" in first[0].caption
    assert "ai agent vs zapier" in second[0].caption
    assert "static brand keyword" not in first[0].caption


def test_coverage_is_recorded_for_every_page_of_the_campaign(tmp_path):
    from agenticcore.research import TerritoryStore, parse_territory

    brands = write_brand(tmp_path, media="none", channels=[
        {"channel": "linkedin", "label": "LI"},
        {"channel": "facebook", "label": "FB"},
    ])
    store = DraftStore(tmp_path / "d.db")
    territory = TerritoryStore(tmp_path / "d.db")
    territory.add_all(parse_territory("TERM: how much does an ai agency cost\n", "acme", []))

    pipeline = ContentPipeline(
        brands, store, MarketingOrchestrator(llm=EchoLLMClient()),
        FakeTelegramBot(), FakeAyrshareClient(), OfflineImageGenerator(),
        None, None, None, False, None, territory,
    )
    drafts = pipeline.queue_campaign("acme")

    covered = territory.for_brand("acme")[0]
    assert covered.times_covered == len(drafts) == 2


def test_no_territory_falls_back_to_the_brand_keyword(tmp_path):
    pipeline, _, _, _ = make_pipeline(tmp_path, keywords=["ai marketing automation"])

    drafts = pipeline.queue_campaign("acme")

    assert "ai marketing automation" in drafts[0].caption


def _with_signals_and_opportunities(tmp_path):
    from agenticcore.research import (
        OpportunityStore, SignalStore, parse_opportunities, parse_signals,
    )

    brands = write_brand(tmp_path, media="none",
                         channels=[{"channel": "linkedin", "label": "LI"}])
    store = DraftStore(tmp_path / "d.db")
    ops = OpportunityStore(tmp_path / "d.db")
    ops.add_all(parse_opportunities(
        "QUERY: an evergreen question worth answering\n", "acme", []))
    sigs = SignalStore(tmp_path / "d.db")
    sigs.add_all(parse_signals(
        "HEADLINE: a vendor shipped something this week\nURGENCY: 5\n"
        "WHY: buyers will ask about it\n", "acme", []))

    pipeline = ContentPipeline(
        brands, store, MarketingOrchestrator(llm=EchoLLMClient()),
        FakeTelegramBot(), FakeAyrshareClient(), OfflineImageGenerator(),
        None, None, None, False, ops, None, sigs,
    )
    return pipeline, ops, sigs


def test_a_live_signal_outranks_an_evergreen_opportunity(tmp_path):
    """The window on a signal closes; a researched question keeps."""

    pipeline, ops, sigs = _with_signals_and_opportunities(tmp_path)

    drafts = pipeline.queue_campaign("acme")

    assert "a vendor shipped something this week" in drafts[0].caption
    assert "an evergreen question" not in drafts[0].caption
    # And the opportunity was not spent while the signal took its place.
    assert ops.next_open("acme") is not None
    assert sigs.next_live("acme") is None


def test_once_signals_run_out_the_evergreen_queue_resumes(tmp_path):
    pipeline, ops, _ = _with_signals_and_opportunities(tmp_path)

    pipeline.queue_campaign("acme")          # spends the signal
    second = pipeline.queue_campaign("acme")  # falls back

    assert "an evergreen question worth answering" in second[0].caption
    assert ops.next_open("acme") is None


def test_publishing_stamps_when_it_actually_went_out(tmp_path):
    """updated_at moves on every edit, so timing analysis needs its own stamp."""

    pipeline, bot, _, store = make_pipeline(tmp_path)
    drafts = pipeline.queue_campaign("acme")
    assert drafts[0].published_at is None

    bot.queue_decision(drafts[0].id, "approve")
    pipeline.process_decisions()

    published = store.get(drafts[0].id)
    assert published.published_at and published.published_at.startswith("20")
