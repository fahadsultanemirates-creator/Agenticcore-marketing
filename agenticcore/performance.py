"""Turns published-post analytics into something the agents can learn from.

Three jobs, in order:

1. **Normalize.** Every network reports its own shape — YouTube average
   watch time, Facebook reaction breakdowns, TikTok video views. Comparing
   them needs a common core, so we pull impressions / likes / comments /
   shares / clicks out of whatever nesting the payload arrived in, and keep
   the raw JSON alongside for anything the core misses.

2. **Store.** One row per draft in the same SQLite file the approval queue
   uses, refreshed in place as a post accumulates views.

3. **Summarize.** Produce the short text block that goes into the next
   campaign's strategy prompt. This is the part that compounds: without it
   every campaign starts from zero knowledge of the audience, forever.

Ranking is by **engagement rate, not raw impressions**. A 200-follower
Telegram channel and a 20,000-follower LinkedIn page are not comparable on
volume, and ranking on volume would simply relearn which page is biggest
every single week.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS post_metrics (
    draft_id TEXT PRIMARY KEY,
    brand_slug TEXT NOT NULL,
    channel TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    raw TEXT,
    collected_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

#: Candidate field names per metric, in priority order. Networks disagree on
#: naming and Ayrshare passes each one's vocabulary through, so rather than
#: maintaining a mapping per platform — which silently rots as networks
#: rename things — we look for any of these anywhere in the payload.
METRIC_FIELDS: dict[str, tuple[str, ...]] = {
    "impressions": (
        "impressions", "impressionCount", "impressionsCount",
        "views", "viewCount", "videoViews", "videoViewCount",
        "playCount", "plays", "reach",
    ),
    "likes": (
        "likeCount", "likes", "favoriteCount", "favorites",
        "reactionCount", "reactions", "diggCount",
    ),
    "comments": ("commentCount", "commentsCount", "comments", "replyCount", "replies"),
    "shares": (
        "shareCount", "sharesCount", "shares",
        "retweetCount", "retweets", "repostCount", "reposts",
    ),
    "clicks": (
        "clickCount", "clicks", "linkClicks", "linkClickCount", "websiteClicks",
    ),
}

CORE_METRICS = tuple(METRIC_FIELDS)


@dataclass
class PostMetrics:
    """One published post's numbers, normalized across networks."""

    draft_id: str
    brand_slug: str
    channel: str
    impressions: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    clicks: int = 0
    raw: Optional[str] = None

    @property
    def engagements(self) -> int:
        return self.likes + self.comments + self.shares + self.clicks

    @property
    def engagement_rate(self) -> float:
        """Engagements per impression.

        Zero when nothing was seen — an unseen post has no rate, and
        reporting one would flatter a post that simply never got shown.
        """

        return self.engagements / self.impressions if self.impressions else 0.0

    def summary_line(self, caption: str = "", caption_chars: int = 110) -> str:
        rate = f"{self.engagement_rate * 100:.1f}%"
        head = (
            f"[{self.channel}] {self.impressions:,} seen, "
            f"{self.engagements:,} engaged ({rate})"
        )
        if not caption:
            return head
        flat = " ".join(caption.split())
        if len(flat) > caption_chars:
            flat = flat[:caption_chars].rstrip() + "…"
        return f'{head}\n    "{flat}"'


def _walk(payload: Any) -> Iterable[tuple[str, Any]]:
    """Yield every (key, value) pair in a nested dict/list structure."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk(item)


def _coerce_number(value: Any) -> Optional[int]:
    """Read a count, including a reaction breakdown like {like: 5, love: 2}."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        parts = [v for v in value.values() if isinstance(v, (int, float))
                 and not isinstance(v, bool)]
        return int(sum(parts)) if parts else None
    return None


def extract_metrics(payload: Any, draft_id: str, brand_slug: str, channel: str) -> PostMetrics:
    """Pull the comparable core out of one platform's analytics payload."""

    found: dict[str, int] = {}
    pairs = list(_walk(payload))
    for metric, candidates in METRIC_FIELDS.items():
        for candidate in candidates:
            # Priority order matters: "impressions" should win over "reach"
            # when a network reports both.
            hit = next(
                (v for k, v in pairs
                 if k == candidate and _coerce_number(v) is not None),
                None,
            )
            if hit is not None:
                found[metric] = _coerce_number(hit)
                break

    return PostMetrics(
        draft_id=draft_id,
        brand_slug=brand_slug,
        channel=channel,
        raw=json.dumps(payload)[:20000],
        **{metric: found.get(metric, 0) for metric in CORE_METRICS},
    )


class MetricsStore:
    """Per-post numbers, in the same SQLite file as the approval queue."""

    def __init__(self, path: str | Path = "agenticcore.db"):
        self.path = str(path)
        with closing(self._connect()) as conn:
            conn.execute(METRICS_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, metrics: PostMetrics) -> None:
        """Insert or refresh one post's numbers; a post keeps accruing views."""

        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO post_metrics
                   (draft_id, brand_slug, channel, impressions, likes, comments,
                    shares, clicks, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(draft_id) DO UPDATE SET
                     impressions=excluded.impressions, likes=excluded.likes,
                     comments=excluded.comments, shares=excluded.shares,
                     clicks=excluded.clicks, raw=excluded.raw,
                     collected_at=datetime('now')""",
                (metrics.draft_id, metrics.brand_slug, metrics.channel,
                 metrics.impressions, metrics.likes, metrics.comments,
                 metrics.shares, metrics.clicks, metrics.raw),
            )
            conn.commit()

    def for_brand(self, brand_slug: str) -> list[PostMetrics]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM post_metrics WHERE brand_slug = ? ORDER BY collected_at DESC",
                (brand_slug,),
            ).fetchall()
        return [
            PostMetrics(
                draft_id=r["draft_id"], brand_slug=r["brand_slug"], channel=r["channel"],
                impressions=r["impressions"], likes=r["likes"], comments=r["comments"],
                shares=r["shares"], clicks=r["clicks"], raw=r["raw"],
            )
            for r in rows
        ]


#: Below this many measured posts, a "top performer" is noise. Telling the
#: strategist to imitate the best of two posts would bake one random result
#: into the brand's voice permanently.
MIN_POSTS_FOR_SIGNAL = 4


class PerformanceMemory:
    """What this brand has already published, and how it did.

    The two halves feed different agents. ``strategy_digest`` tells the
    strategist what this audience responded to; ``recent_captions`` tells the
    post writer what it has already said, so a system running weekly for six
    months stops quietly converging on the same three hooks.
    """

    def __init__(self, drafts, metrics: MetricsStore):
        self.drafts = drafts
        self.metrics = metrics

    def recent_captions(self, brand_slug: str, limit: int = 8) -> list[str]:
        published = self.drafts.list_for_brand(brand_slug, status="published", limit=limit)
        return [d.caption for d in published if d.caption]

    def strategy_digest(self, brand_slug: str, top_n: int = 3) -> str:
        """A short readout of best and worst posts, or "" when there's no signal.

        Returns empty rather than a hedge so callers can simply omit the
        section: an empty string keeps it out of the prompt entirely, where
        "no data available" would spend tokens telling the model nothing.
        """

        measured = [m for m in self.metrics.for_brand(brand_slug) if m.impressions > 0]
        if len(measured) < MIN_POSTS_FOR_SIGNAL:
            return ""

        captions = {
            d.id: d.caption
            for d in self.drafts.list_for_brand(brand_slug, status="published")
        }
        ranked = sorted(measured, key=lambda m: m.engagement_rate, reverse=True)
        best, worst = ranked[:top_n], ranked[-top_n:]

        lines = [
            f"How this brand's last {len(measured)} measured posts actually did, "
            f"ranked by engagement rate (engagements per impression, so pages "
            f"of different sizes compare fairly):",
            "",
            "BEST PERFORMING — the angles and tones this audience responded to:",
        ]
        lines += [f"  {m.summary_line(captions.get(m.draft_id, ''))}" for m in best]
        lines += ["", "WEAKEST — treat these as angles to avoid repeating:"]
        lines += [f"  {m.summary_line(captions.get(m.draft_id, ''))}" for m in worst]
        return "\n".join(lines)


#: Below this many measured posts in a slot, a "best time" is one lucky post.
MIN_POSTS_PER_SLOT = 3

#: And below this many overall, the comparison between slots is meaningless.
MIN_POSTS_FOR_TIMING = 8

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _slot(published_at: str) -> Optional[tuple[int, int]]:
    """(weekday, hour) in UTC from a stored ISO timestamp."""

    from datetime import datetime

    try:
        stamp = datetime.fromisoformat(published_at)
    except (TypeError, ValueError):
        return None
    return stamp.weekday(), stamp.hour


def timing_report(drafts, metrics: MetricsStore, brand_slug: str) -> str:
    """When this brand's posts actually land, learned from its own results.

    Returns "" rather than a guess when the sample is too thin. Generic
    "best times to post" advice is an average over everyone else's audience;
    the only version worth acting on is the one measured on yours, and that
    needs enough posts to mean anything.
    """

    published = {d.id: d for d in drafts.list_for_brand(brand_slug, status="published")}
    measured = [m for m in metrics.for_brand(brand_slug) if m.impressions > 0]

    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    counted = 0
    for m in measured:
        draft = published.get(m.draft_id)
        if not draft or not draft.published_at:
            continue
        slot = _slot(draft.published_at)
        if slot is None:
            continue
        buckets[slot].append(m.engagement_rate)
        counted += 1

    if counted < MIN_POSTS_FOR_TIMING:
        return ""

    ranked = [
        (slot, sum(rates) / len(rates), len(rates))
        for slot, rates in buckets.items()
        if len(rates) >= MIN_POSTS_PER_SLOT
    ]
    if not ranked:
        return ""

    ranked.sort(key=lambda row: -row[1])
    lines = [
        f"When {brand_slug}'s posts land, from {counted} measured post(s) "
        f"(times are UTC; only slots with {MIN_POSTS_PER_SLOT}+ posts shown):",
    ]
    for (day, hour), rate, n in ranked[:5]:
        lines.append(f"  {DAY_NAMES[day]} {hour:02d}:00 — {rate * 100:.1f}% across {n} post(s)")
    if len(ranked) > 1:
        best, worst = ranked[0], ranked[-1]
        lines.append(
            f"  Best slot outperforms the weakest by "
            f"{best[1] / worst[1]:.1f}x." if worst[1] else ""
        )
    return "\n".join(l for l in lines if l)
