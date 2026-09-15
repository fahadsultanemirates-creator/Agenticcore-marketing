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


def make_pipeline(tmp_path):
    brands = write_brand(tmp_path)
    store = DraftStore(tmp_path / "drafts.db")
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient())
    bot = FakeTelegramBot()
    publisher = FakeAyrshareClient()
    pipeline = ContentPipeline(brands, store, orchestrator, bot, publisher, OfflineImageGenerator())
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
