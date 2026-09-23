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
from agenticcore.reach import LINK_FIRST_COMMENT, rules_for, split_link
from agenticcore.topics import TopicLedger, topic_key
from agenticcore.video import VideoPackage, VideoPackageAgent

#: Where an auto-chosen topic can come from, best first. A live signal beats
#: a researched question beats an uncovered search term beats an angle the
#: website itself suggests — but any of them beats asking a model to invent
#: a subject with nothing to go on.
TOPIC_SOURCES = ("signal", "opportunity", "territory", "website")

#: How many earlier posts the writer is shown to avoid repeating, and how
#: much of each. Unbounded, a ten-post batch has its last prompt carrying
#: nineteen full captions — expensive, and it dilutes the instruction that
#: matters. What actually repeats is the opening, so a window of openings
#: says more than a wall of complete posts.
AVOID_WINDOW = 8
AVOID_CHARS = 220


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
    #: How many of the requested items had no fresh topic left. Surfaced
    #: rather than silently filled, because the honest answer to "make five
    #: more" on an exhausted site is that there are not five more — not five
    #: posts about nothing.
    without_topic: int = 0
    #: Whether this site has ever had a topic source at all. Running out
    #: after covering everything and never having had anything are different
    #: problems with different fixes, so they are not reported as one.
    had_sources: bool = True

    @property
    def exhausted(self) -> bool:
        """Ran out of fresh topics, having had some."""

        return self.without_topic > 0 and self.had_sources

    @property
    def unresearched(self) -> bool:
        """Never had a topic source — no site profile, no research."""

        return self.without_topic > 0 and not self.had_sources

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
        ledger: Optional[TopicLedger] = None,
    ):
        self.brands = brands
        self.orchestrator = orchestrator
        self.websites = websites
        self.store = store
        self.opportunities = opportunities
        self.territory = territory
        self.signals = signals
        self.critique = critique
        # Topic memory is its own store, not a column on drafts: a video has
        # no caption and no channel but spends a topic exactly as a post does.
        self.ledger = ledger
        self.video_agent = VideoPackageAgent(orchestrator.reach_critic.llm)

    # -- context -------------------------------------------------------

    def website_brief(self, brand_slug: str) -> str:
        profile = self.websites.get(brand_slug) if self.websites else None
        return profile.as_brief() if profile else ""

    def used_topics(self, brand_slug: str) -> set[str]:
        """Topic keys this site has already been written about."""

        # The ledger is the single source of truth when there is one, so
        # clearing it genuinely clears. Unioning it with draft history left
        # topics blocked by old drafts after a reset, which makes a reset
        # button that does not reset. Drafts are only a fallback for a
        # studio wired without a ledger.
        if self.ledger:
            return self.ledger.used_keys(brand_slug)
        if self.store:
            return {_topic_key(d.topic)
                    for d in self.store.list_for_brand(brand_slug) if d.topic}
        return set()

    def _spend(self, brand_slug: str, topic: str, kind: str, source: str) -> None:
        if self.ledger and topic:
            self.ledger.mark(brand_slug, topic, kind=kind, source=source)

    def free_topics(self, brand_slug: str) -> int:
        """Let this site be written about from the start again."""

        return self.ledger.reset(brand_slug) if self.ledger else 0

    def recent_captions(self, brand_slug: str, limit: int = AVOID_WINDOW) -> list[str]:
        if not self.store:
            return []
        return [_opening(d.caption)
                for d in self.store.list_for_brand(brand_slug, limit=limit)
                if d.caption]

    def choose_topics(self, brand_slug: str, count: int,
                      allow_repeats: bool = False) -> list[tuple[str, str]]:
        """Pick ``count`` distinct topics, best source first.

        Returns (topic, source) pairs. Falls back to an empty topic rather
        than inventing one: a post written from the website brief alone is
        still grounded, while a fabricated subject is not.
        """

        chosen: list[tuple[str, str]] = []
        # Seed with what this brand has already been written about, so
        # asking again moves on instead of re-offering the same ideas.
        # Without this "give me a different batch" returns the same batch.
        seen: set[str] = set() if allow_repeats else set(self.used_topics(brand_slug))

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

    @staticmethod
    def _count_open(topics: list[tuple[str, str]]) -> int:
        return sum(1 for topic, source in topics if not topic and source == "open")

    def has_sources(self, brand_slug: str) -> bool:
        """Is there anything at all this site could be written about?"""

        if self.used_topics(brand_slug):
            return True
        if self.signals and self.signals.live(brand_slug):
            return True
        if self.opportunities and self.opportunities.for_brand(brand_slug, status="open"):
            return True
        if self.territory and self.territory.for_brand(brand_slug):
            return True
        profile = self.websites.get(brand_slug) if self.websites else None
        return bool(profile and profile.topics)

    # -- generation ----------------------------------------------------

    #: Phrasings a writer uses to promise a link it did not actually write.
    _LINK_PROMISES = ("first comment", "link below", "link in the comments",
                      "comment below for the link", "link in bio")

    def _promised_link(self, brand_slug: str, platform: str, caption: str,
                       found: Optional[str]) -> Optional[str]:
        """Supply the site URL when a post promises a link but carries none.

        On platforms where a link in the body costs reach, the writer is
        told to point at the first comment — and it often writes that
        sentence without ever including a URL. The post then makes a promise
        the output cannot keep, and you would have to remember the link
        yourself every time. Where the site is known, fill it in.
        """

        if found or rules_for(platform).link_policy != LINK_FIRST_COMMENT:
            return found
        lowered = caption.lower()
        if not any(phrase in lowered for phrase in self._LINK_PROMISES):
            return None
        profile = self.websites.get(brand_slug) if self.websites else None
        return profile.url if profile and profile.url else None

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
        allow_repeats: bool = False,
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
        # A topic named outright always wins: asking for it IS the asking.
        topics = ([(topic, "given")] * count if topic
                  else self.choose_topics(brand_slug, count, allow_repeats))

        # One strategy for the batch: every post shares the brand's
        # positioning, and re-deriving it per post costs a call for nothing.
        strategy = self.orchestrator.run_strategy(
            self._brief(brand), topic=website
        ).output

        batch = PostBatch(brand_slug=brand_slug,
                          without_topic=self._count_open(topics),
                          had_sources=self.has_sources(brand_slug))
        for index in range(count):
            target = targets[index % len(targets)]
            subject, source = topics[index]
            combined = "\n\n".join(p for p in (website, subject) if p)

            avoid = (recent + [_opening(p.caption) for p in batch.posts])[-AVOID_WINDOW:]
            caption = self.orchestrator.draft_post(
                self._brief(brand), strategy, target.channel, target.label,
                recent_captions=avoid,
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
            first_comment = self._promised_link(
                brand_slug, target.channel, caption, first_comment
            )
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
            self._spend(brand_slug, post.topic, "post", source)

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
        allow_repeats: bool = False,
    ) -> PostBatch:
        """Write ``count`` video packages — script, thumbnail, caption."""

        brand = self.brands.get(brand_slug)
        website = self.website_brief(brand_slug)
        topics = ([(topic, "given")] * count if topic
                  else self.choose_topics(brand_slug, count, allow_repeats))

        batch = PostBatch(brand_slug=brand_slug,
                          without_topic=self._count_open(topics),
                          had_sources=self.has_sources(brand_slug))
        for subject, source in topics:
            package = self.video_agent.write(
                brand, seconds,
                topic=_headline(subject),
                website_brief=website,
                avoid=batch.topics_used + [v.script[:120] for v in batch.videos],
            )
            batch.videos.append(package)
            batch.topics_used.append(package.topic)
            self._spend(brand_slug, package.topic, "video", source)
        return batch


def _opening(caption: str) -> str:
    """The part of a post that actually repeats — its first lines."""

    text = " ".join((caption or "").split())
    return text[:AVOID_CHARS] + ("…" if len(text) > AVOID_CHARS else "")


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
