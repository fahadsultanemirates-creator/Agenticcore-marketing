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

    ContentPipeline.collect_metrics()
        -> reads back how published posts performed, and stores it

The loop closes through PerformanceMemory: collect_metrics records results,
and the next queue_campaign feeds the best and worst of them back into the
strategist, plus recent captions into the post writer so it stops repeating
itself. Without that feedback every campaign is the brand's first.
"""

from __future__ import annotations

from typing import Optional

from agenticcore.approval.telegram_bot import TelegramApprovalBot
from agenticcore.brands import BrandRegistry
from agenticcore.brands import MEDIA_IMAGE, MEDIA_VIDEO
from agenticcore.creative.base import ImageGenerator, VideoGenerator
from agenticcore.orchestrator import CampaignBrief, MarketingOrchestrator
from agenticcore.performance import PerformanceMemory, extract_metrics
from agenticcore.reach import split_link
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
        memory: Optional[PerformanceMemory] = None,
        analytics=None,
        critique_reach: bool = True,
    ):
        self.brands = brands
        self.store = store
        self.orchestrator = orchestrator
        self.bot = bot
        self.publisher = publisher
        self.image_generator = image_generator
        self.video_generator = video_generator
        # Optional so the pipeline still runs on day one, before any post
        # has been published and there is nothing to learn from yet.
        self.memory = memory
        self.analytics = analytics
        # One extra model call per post. Left on by default because a post
        # nobody is shown cost more to produce than the critique does.
        self.critique_reach = critique_reach

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
        digest = self.memory.strategy_digest(brand.slug) if self.memory else ""
        recent = self.memory.recent_captions(brand.slug) if self.memory else []

        strategy = self.orchestrator.run_strategy(brief, performance=digest).output
        assets = self._generate_media(brand, brief, strategy, image_prompt)

        drafts = []
        for target in brand.channels:
            caption, score = self._write_for_reach(
                brief, strategy, target, recent, brand.primary_keyword
            )
            # Link placement is enforced here, not asked of the writer: the
            # reach penalty is mechanical and a writer polishing a sentence
            # will quietly drop a rule it was told once.
            caption, first_comment = split_link(caption, target.channel)

            wanted = brand.media_for(target)
            draft = self.store.create(
                brand_slug=brand.slug,
                channel=target.channel,
                caption=caption,
                image_path=assets.get(MEDIA_IMAGE) if wanted == MEDIA_IMAGE else None,
                video_path=assets.get(MEDIA_VIDEO) if wanted == MEDIA_VIDEO else None,
                status="pending_approval",
            )
            self.store.update(draft.id, reach_score=score, first_comment=first_comment)
            draft.reach_score, draft.first_comment = score, first_comment
            self.store.update(draft.id, telegram_chat_id=brand.telegram_chat_id)
            draft.telegram_chat_id = brand.telegram_chat_id

            message_id = self.bot.send_for_approval(
                draft, draft.telegram_caption(brand.name, target.label or target.channel)
            )
            self.store.update(draft.id, telegram_message_id=message_id)
            drafts.append(draft)
        return drafts

    def _write_for_reach(
        self,
        brief: CampaignBrief,
        strategy: str,
        target,
        recent: list[str],
        keyword: Optional[str],
    ) -> tuple[str, Optional[int]]:
        """Draft a post, then let the reach critic replace a weak one.

        Returns the caption to queue and the score it earned. The score is
        stored so it can later be compared against what the post actually
        achieved — that is what keeps the critic honest rather than a second
        opinion nobody checks.
        """

        caption = self.orchestrator.draft_post(
            brief, strategy, target.channel, target.label,
            recent_captions=recent, keyword=keyword,
        ).output

        if not self.critique_reach:
            return caption, None

        verdict = self.orchestrator.critique_reach(caption, target.channel, keyword)
        if verdict.should_revise:
            return verdict.revised, verdict.score
        return caption, verdict.score

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

    def collect_metrics(self, brand_slug: Optional[str] = None) -> list:
        """Pull analytics for published posts and record them.

        Safe to run on a schedule: a post keeps accruing views for days, so
        each run refreshes the numbers in place rather than adding rows. One
        post's analytics failing — a network lagging, a deleted post — never
        stops the rest being collected.
        """

        if self.analytics is None or self.memory is None:
            return []

        slugs = [brand_slug] if brand_slug else [b.slug for b in self.brands.all()]
        collected = []
        for slug in slugs:
            brand = self.brands.get(slug)
            for draft in self.store.list_for_brand(slug, status="published"):
                if not draft.published_post_id:
                    continue
                try:
                    payload = self.analytics.post_analytics(
                        draft.published_post_id,
                        platforms=[draft.channel],
                        profile_key=brand.ayrshare_profile_key,
                    )
                except Exception as exc:  # noqa: BLE001 - one post must not stop the sweep
                    print(f"  analytics failed for {draft.id[:8]} ({draft.channel}): {exc}")
                    continue
                metrics = extract_metrics(payload, draft.id, slug, draft.channel)
                self.memory.metrics.record(metrics)
                collected.append(metrics)
        return collected

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

        if draft.first_comment:
            # The link was pulled out of the body to avoid the reach penalty;
            # it only reaches anyone if it lands as a comment. A failure here
            # must not un-publish a post that already went out.
            try:
                self.publisher.comment(
                    post_id=post_id,
                    comment=draft.first_comment,
                    platforms=[draft.channel],
                    profile_key=brand.ayrshare_profile_key,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  first comment failed for {draft.id[:8]}: {exc}")
