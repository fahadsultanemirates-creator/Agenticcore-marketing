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

    def publish(self, caption, platforms, media_urls=None, profile_key=None):
        self.calls.append(
            {"caption": caption, "platforms": platforms, "media_urls": media_urls, "profile_key": profile_key}
        )
        return {"status": "success", "id": f"fake-post-{len(self.calls)}"}


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
