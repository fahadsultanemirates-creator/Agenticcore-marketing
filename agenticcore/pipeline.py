"""End-to-end flow: generate -> queue for Telegram approval -> publish.

    ContentPipeline.queue_campaign(brand_slug)
        -> runs the strategist once for that brand
        -> generates the one asset its pages share (image or video,
           per the brand's `media` setting)
        -> drafts one post per configured channel (page), written for
           that platform specifically
        -> creates one PostDraft per configured channel (page)
        -> sends each to Telegram with Approve/Reject buttons

    ContentPipeline.process_decisions()
        -> polls Telegram for button taps
        -> on Approve: publishes that one draft to that one page via Ayrshare
        -> on Reject: marks it rejected, nothing is published
"""

from __future__ import annotations

from typing import Optional

from agenticcore.approval.telegram_bot import TelegramApprovalBot
from agenticcore.brands import BrandRegistry
from agenticcore.brands import MEDIA_IMAGE, MEDIA_VIDEO
from agenticcore.creative.base import ImageGenerator, VideoGenerator
from agenticcore.orchestrator import CampaignBrief, MarketingOrchestrator
from agenticcore.publishing.ayrshare import AyrshareClient
from agenticcore.queue import DraftStore, PostDraft


class ContentPipeline:
    def __init__(
        self,
        brands: BrandRegistry,
        store: DraftStore,
        orchestrator: MarketingOrchestrator,
        bot: TelegramApprovalBot,
        publisher: AyrshareClient,
        image_generator: Optional[ImageGenerator] = None,
        video_generator: Optional[VideoGenerator] = None,
    ):
        self.brands = brands
        self.store = store
        self.orchestrator = orchestrator
        self.bot = bot
        self.publisher = publisher
        self.image_generator = image_generator
        self.video_generator = video_generator

    def trust_configured_chats(self) -> None:
        """Authorize every brand's approval chat up front.

        queue_campaign trusts a brand's chat as it generates for it, which is
        no help to a process that only listens: it never generates, so
        without this a tap on a draft queued by an earlier run would be
        refused as unauthorized.
        """

        for brand in self.brands.all():
            self.bot.add_authorized_chat(brand.telegram_chat_id)

    def queue_campaign(self, brand_slug: str, image_prompt: Optional[str] = None) -> list[PostDraft]:
        brand = self.brands.get(brand_slug)
        self.bot.add_authorized_chat(brand.telegram_chat_id)
        brief = CampaignBrief(
            product=brand.name,
            audience=brand.audience,
            goal=brand.goal or f"Grow {brand.name}",
            tone=brand.tone,
            channels=["social"],
        )
        # One strategy for the brand, then one post per page written against
        # it. Drafting per page costs an extra call each but is the whole
        # point: an Instagram caption and a LinkedIn post are not the same
        # text, and whatever lands here gets published verbatim.
        strategy = self.orchestrator.run_strategy(brief).output
        assets = self._generate_media(brand, brief, strategy, image_prompt)

        drafts = []
        for target in brand.channels:
            caption = self.orchestrator.draft_post(
                brief, strategy, target.channel, target.label
            ).output
            wanted = brand.media_for(target)
            draft = self.store.create(
                brand_slug=brand.slug,
                channel=target.channel,
                caption=caption,
                image_path=assets.get(MEDIA_IMAGE) if wanted == MEDIA_IMAGE else None,
                video_path=assets.get(MEDIA_VIDEO) if wanted == MEDIA_VIDEO else None,
                status="pending_approval",
            )
            self.store.update(draft.id, telegram_chat_id=brand.telegram_chat_id)
            draft.telegram_chat_id = brand.telegram_chat_id

            message_id = self.bot.send_for_approval(
                draft, draft.telegram_caption(brand.name, target.label or target.channel)
            )
            self.store.update(draft.id, telegram_message_id=message_id)
            drafts.append(draft)
        return drafts

    def _generate_media(
        self,
        brand,
        brief: CampaignBrief,
        strategy: str,
        image_prompt: Optional[str],
    ) -> dict[str, str]:
        """Produce each distinct asset this brand's pages ask for, once.

        A brand spanning YouTube and LinkedIn needs a video and a graphic —
        but one of each, shared by the pages that want them, not one per
        page. Media a brand asks for with no backend configured is simply
        absent from the result: an unset HEYGEN_API_KEY should cost you the
        video, not the campaign.
        """

        wanted = brand.required_media_types()
        assets: dict[str, str] = {}

        if MEDIA_VIDEO in wanted and self.video_generator is not None:
            script = self.orchestrator.draft_video_script(brief, strategy).output
            assets[MEDIA_VIDEO] = self.video_generator.generate_video(script)

        if MEDIA_IMAGE in wanted and self.image_generator is not None:
            assets[MEDIA_IMAGE] = self.image_generator.generate_image(
                image_prompt
                or f"Social media graphic for {brand.name}. Audience: {brand.audience}."
            )

        return assets

    def queue_all(self, image_prompt: Optional[str] = None) -> list[PostDraft]:
        """Run a campaign for every brand in the registry."""

        drafts = []
        for brand in self.brands.all():
            drafts.extend(self.queue_campaign(brand.slug, image_prompt=image_prompt))
        return drafts

    def pending_drafts(self) -> list[PostDraft]:
        """Drafts still waiting on a tap, e.g. queued before a restart."""

        return self.store.list_by_status("pending_approval")

    def process_decisions(self, timeout: int = 30) -> list[PostDraft]:
        """Poll Telegram once (long-poll up to ``timeout`` seconds) and act on any taps."""

        handled = []
        for draft_id, action in self.bot.poll_decisions(timeout=timeout):
            draft = self.store.get(draft_id)
            if draft is None or draft.status != "pending_approval":
                continue

            if action == "approve":
                self._publish(draft)
            else:
                self.store.update(draft.id, status="rejected")
                draft.status = "rejected"
            handled.append(draft)
        return handled

    def _publish(self, draft: PostDraft) -> None:
        brand = self.brands.get(draft.brand_slug)
        media_url = draft.image_path or draft.video_path
        result = self.publisher.publish(
            caption=draft.caption,
            platforms=[draft.channel],
            media_urls=[media_url] if media_url else None,
            profile_key=brand.ayrshare_profile_key,
        )
        post_id = result.get("id")
        self.store.update(draft.id, status="published", published_post_id=post_id)
        draft.status, draft.published_post_id = "published", post_id
