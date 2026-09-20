"""On-demand post creation: N posts for one website, text or video.

This is the framework as a post-creation specialist rather than a publisher.
You pick a site, say how many you want and optionally what about, and get a
batch back to choose from. Nothing is published; nothing is queued for
approval. You copy what you like and discard the rest.

Two things make a batch useful rather than N near-identical posts:

- **Each post gets its own topic.** Asked for five posts on one subject a
  model produces five rewrites of the same post. Asked for five posts on
  five subjects it produces five posts. So topics are chosen first, from
  what the framework knows, and only then is each one written.
- **The batch remembers itself.** Every post is written knowing the angles
  already used in this batch and recently published, so asking again gives
  you different material rather than the same ideas reshuffled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from agenticcore.brands import MEDIA_VIDEO, BrandProfile
from agenticcore.orchestrator import CampaignBrief
from agenticcore.reach import split_link
from agenticcore.video import VideoPackage, VideoPackageAgent

#: Where an auto-chosen topic can come from, best first. A live signal beats
#: a researched question beats an uncovered search term beats an angle the
#: website itself suggests — but any of them beats asking a model to invent
#: a subject with nothing to go on.
TOPIC_SOURCES = ("signal", "opportunity", "territory", "website")


@dataclass
class GeneratedPost:
    """One finished post, ready to copy."""

    id: str
    brand_slug: str
    platform: str
    caption: str
    topic: str = ""
    topic_source: str = ""
    reach_score: Optional[int] = None
    #: True when the critic's rewrite replaced the original. The score is
    #: the ORIGINAL's, so showing it unqualified next to a post that was
    #: rewritten to fix those very problems misreads as a weak final post.
    revised: bool = False
    first_comment: Optional[str] = None

    def as_telegram_message(self, index: int = 0, total: int = 0) -> str:
        head = f"[{self.platform}]"
        if total:
            head = f"[{index}/{total} · {self.platform}]"
        if self.reach_score is not None:
            head += (f" reach {self.reach_score}/10 → rewritten" if self.revised
                     else f" reach {self.reach_score}/10")
        if self.topic:
            head += f"\n{self.topic}"

        body = f"{head}\n\n{self.caption}"
        if self.first_comment:
            body += f"\n\nFIRST COMMENT (post separately — a link in the body costs reach):\n{self.first_comment}"
        return body


@dataclass
class PostBatch:
    """What one request produced."""

    brand_slug: str
    posts: list[GeneratedPost] = field(default_factory=list)
    videos: list[VideoPackage] = field(default_factory=list)
    topics_used: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.posts) + len(self.videos)


class PostStudio:
    """Makes posts on request. Knows the brands, the sites, and what's been said."""

    def __init__(
        self,
        brands,
        orchestrator,
        websites=None,
        store=None,
        opportunities=None,
        territory=None,
        signals=None,
        critique: bool = True,
    ):
        self.brands = brands
        self.orchestrator = orchestrator
        self.websites = websites
        self.store = store
        self.opportunities = opportunities
        self.territory = territory
        self.signals = signals
        self.critique = critique
        self.video_agent = VideoPackageAgent(orchestrator.reach_critic.llm)

    # -- context -------------------------------------------------------

    def website_brief(self, brand_slug: str) -> str:
        profile = self.websites.get(brand_slug) if self.websites else None
        return profile.as_brief() if profile else ""

    def used_topics(self, brand_slug: str) -> set[str]:
        """Topic keys this brand's earlier posts already covered."""

        if not self.store:
            return set()
        return {
            _topic_key(d.topic)
            for d in self.store.list_for_brand(brand_slug) if d.topic
        }

    def recent_captions(self, brand_slug: str, limit: int = 10) -> list[str]:
        if not self.store:
            return []
        return [d.caption for d in self.store.list_for_brand(brand_slug, limit=limit)
                if d.caption]

    def choose_topics(self, brand_slug: str, count: int) -> list[tuple[str, str]]:
        """Pick ``count`` distinct topics, best source first.

        Returns (topic, source) pairs. Falls back to an empty topic rather
        than inventing one: a post written from the website brief alone is
        still grounded, while a fabricated subject is not.
        """

        chosen: list[tuple[str, str]] = []
        # Seed with what this brand has already been written about, so
        # asking again moves on instead of re-offering the same ideas.
        # Without this "give me a different batch" returns the same batch.
        seen: set[str] = set(self.used_topics(brand_slug))

        def take(text: str, source: str) -> None:
            key = _topic_key(_headline(text))
            if text and key not in seen and len(chosen) < count:
                seen.add(key)
                chosen.append((text, source))

        if self.signals:
            for s in self.signals.live(brand_slug):
                take(s.as_brief(), "signal")
        if self.opportunities:
            for o in self.opportunities.for_brand(brand_slug, status="open"):
                take(o.as_brief(), "opportunity")
        if self.territory:
            for t in self.territory.for_brand(brand_slug):
                if not t.is_covered:
                    take(f"Answer the search '{t.term}'. {t.rationale}".strip(), "territory")
        if self.websites:
            profile = self.websites.get(brand_slug)
            if profile:
                for angle in profile.topics:
                    take(angle, "website")

        while len(chosen) < count:
            chosen.append(("", "open"))
        return chosen

    # -- generation ----------------------------------------------------

    def _brief(self, brand: BrandProfile) -> CampaignBrief:
        return CampaignBrief(
            product=brand.name, audience=brand.audience,
            goal=brand.goal or f"Grow {brand.name}",
            tone=brand.tone, channels=["social"],
        )

    def text_platforms(self, brand: BrandProfile) -> list:
        return [t for t in brand.channels if brand.media_for(t) != MEDIA_VIDEO]

    def make_posts(
        self,
        brand_slug: str,
        count: int = 3,
        platform: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> PostBatch:
        """Write ``count`` posts for one site.

        ``platform`` limits to one page; otherwise the batch spreads across
        that brand's text pages. ``topic`` fixes the subject for all of them
        — which is what you want for a specific announcement, and not what
        you want when browsing for ideas.
        """

        brand = self.brands.get(brand_slug)
        targets = ([t for t in brand.channels if t.channel == platform]
                   if platform else self.text_platforms(brand))
        if not targets:
            raise ValueError(
                f"{brand.name} has no {'page for ' + platform if platform else 'text page'}"
            )

        website = self.website_brief(brand_slug)
        recent = self.recent_captions(brand_slug)
        topics = ([(topic, "given")] * count if topic
                  else self.choose_topics(brand_slug, count))

        # One strategy for the batch: every post shares the brand's
        # positioning, and re-deriving it per post costs a call for nothing.
        strategy = self.orchestrator.run_strategy(
            self._brief(brand), topic=website
        ).output

        batch = PostBatch(brand_slug=brand_slug)
        for index in range(count):
            target = targets[index % len(targets)]
            subject, source = topics[index]
            combined = "\n\n".join(p for p in (website, subject) if p)

            caption = self.orchestrator.draft_post(
                self._brief(brand), strategy, target.channel, target.label,
                recent_captions=recent + [p.caption for p in batch.posts],
                keyword=brand.primary_keyword, topic=combined,
                forbidden=brand.forbidden_claims,
            ).output

            score, revised = None, False
            if self.critique:
                verdict = self.orchestrator.critique_reach(
                    caption, target.channel, brand.primary_keyword
                )
                score = verdict.score
                if verdict.should_revise:
                    caption, revised = verdict.revised, True

            caption, first_comment = split_link(caption, target.channel)
            if brand.disclaimer and brand.disclaimer not in caption:
                caption = f"{caption}\n\n{brand.disclaimer}"

            post = GeneratedPost(
                id=str(uuid.uuid4()), brand_slug=brand_slug,
                platform=target.channel, caption=caption,
                topic=_headline(subject), topic_source=source,
                reach_score=score, revised=revised, first_comment=first_comment,
            )
            batch.posts.append(post)
            batch.topics_used.append(post.topic)

            if self.store:
                draft = self.store.create(
                    brand_slug=brand_slug, channel=target.channel,
                    caption=caption, status="draft",
                )
                self.store.update(draft.id, reach_score=score,
                                  first_comment=first_comment, topic=post.topic)

        return batch

    def make_videos(
        self,
        brand_slug: str,
        count: int = 1,
        seconds: int = 30,
        topic: Optional[str] = None,
    ) -> PostBatch:
        """Write ``count`` video packages — script, thumbnail, caption."""

        brand = self.brands.get(brand_slug)
        website = self.website_brief(brand_slug)
        topics = ([(topic, "given")] * count if topic
                  else self.choose_topics(brand_slug, count))

        batch = PostBatch(brand_slug=brand_slug)
        for subject, _source in topics:
            package = self.video_agent.write(
                brand, seconds,
                topic=_headline(subject),
                website_brief=website,
                avoid=batch.topics_used + [v.script[:120] for v in batch.videos],
            )
            batch.videos.append(package)
            batch.topics_used.append(package.topic)
        return batch


def _topic_key(topic: str) -> str:
    """A comparable form of a topic, so near-identical ones count as one."""

    return " ".join((topic or "").lower().split())[:80]


#: Prefixes the topic briefs add, stripped back off for a readable label.
_TOPIC_PREFIXES = (
    "The question this post must answer:",
    "React to this, while it is still current:",
    "Answer the search",
)


def _headline(topic: str, limit: int = 90) -> str:
    """A short readable label for a topic brief.

    The briefs are written for a model, not for a menu, so the label keeps
    only the first sentence and stops on a word boundary — a caption headed
    with half a word mid-clause looks broken, and this is the line you read
    when choosing which post to keep.
    """

    for line in (topic or "").splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        for prefix in _TOPIC_PREFIXES:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].lstrip(" :")
                break
        # Keep the subject, drop the rationale that follows it.
        cleaned = cleaned.strip("'\" ").split("'. ")[0].split(". ")[0]
        cleaned = cleaned.strip("'\" .")
        if len(cleaned) > limit:
            cleaned = cleaned[:limit].rsplit(" ", 1)[0] + "…"
        return cleaned
    return ""
