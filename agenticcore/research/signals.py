"""Perishable market signals: what just happened, and what rivals just did.

Opportunities and territory are evergreen — a question worth answering this
month is still worth answering next month. Signals are the opposite. A post
reacting to something that happened yesterday earns reach that the identical
post earns none of next week, because timeliness is itself a distribution
advantage.

That difference drives the whole design:

- every signal carries a ``freshness_hours`` shelf life and expires on its own
- live signals **jump the queue** ahead of evergreen topics, because a
  territory gap will still be there tomorrow and a news hook will not
- the queue is ordered by urgency, then by what expires soonest, so nothing
  sits until it rots

One honest limit, enforced in the prompt: web search shows what a competitor
*published*. It does not show how that post performed. Tools that claim
otherwise are guessing, and a fabricated engagement number for a rival is
the same failure as a fabricated statistic about yourself.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agenticcore.research.parsing import parse_fields, split_blocks

SIGNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_signals (
    id TEXT PRIMARY KEY,
    brand_slug TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'event',
    headline TEXT NOT NULL,
    what_happened TEXT,
    why_it_matters TEXT,
    angle TEXT,
    freshness_hours INTEGER NOT NULL DEFAULT 48,
    urgency INTEGER NOT NULL DEFAULT 3,
    sources TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    used_by_draft TEXT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    used_at TEXT
);
"""

KIND_EVENT = "event"
KIND_COMPETITOR = "competitor"
KIND_TREND = "trend"
KINDS = (KIND_EVENT, KIND_COMPETITOR, KIND_TREND)

STATUS_OPEN = "open"
STATUS_USED = "used"
STATUS_EXPIRED = "expired"


@dataclass
class MarketSignal:
    """Something worth reacting to, before the window closes."""

    id: str
    brand_slug: str
    headline: str
    kind: str = KIND_EVENT
    what_happened: str = ""
    why_it_matters: str = ""
    angle: str = ""
    freshness_hours: int = 48
    urgency: int = 3
    sources: list[str] = field(default_factory=list)
    status: str = STATUS_OPEN
    used_by_draft: Optional[str] = None

    def as_brief(self) -> str:
        """The signal as prompt text for the writing agents."""

        lines = [f"React to this, while it is still current: {self.headline}"]
        if self.what_happened:
            lines.append(f"What happened: {self.what_happened}")
        if self.why_it_matters:
            lines.append(f"Why this audience cares: {self.why_it_matters}")
        if self.angle:
            lines.append(f"The angle to take: {self.angle}")
        if self.kind == KIND_COMPETITOR:
            lines.append(
                "This is a competitor observation. You know what they published, "
                "NOT how it performed — never claim engagement numbers for it. "
                "Do not name or attack them; take the better position."
            )
        if self.sources:
            lines.append(
                "Researched from: " + ", ".join(self.sources[:5]) +
                ". Stay within what those support."
            )
        return "\n".join(lines)


SIGNAL_LABELS = ("HEADLINE", "KIND", "WHAT", "WHY", "ANGLE", "URGENCY", "FRESHNESS_HOURS")


def parse_signals(text: str, brand_slug: str, sources: list[str]) -> list[MarketSignal]:
    """Read signals out of the monitoring agent's reply."""

    import re

    found = []
    for block in split_blocks(text, "HEADLINE"):
        fields = parse_fields(block, SIGNAL_LABELS)
        headline = fields.get("headline", "")
        if len(headline) < 10:
            continue

        def _int(label: str, default: int, lo: int, hi: int) -> int:
            raw = re.sub(r"\D", "", fields.get(label, ""))
            try:
                return max(lo, min(hi, int(raw))) if raw else default
            except ValueError:
                return default

        kind = fields.get("kind", KIND_EVENT).strip().lower()
        found.append(MarketSignal(
            id=str(uuid.uuid4()),
            brand_slug=brand_slug,
            headline=headline,
            kind=kind if kind in KINDS else KIND_EVENT,
            what_happened=fields.get("what", ""),
            why_it_matters=fields.get("why", ""),
            angle=fields.get("angle", ""),
            urgency=_int("urgency", 3, 1, 5),
            # Capped at two weeks: anything claiming a longer shelf life is
            # evergreen and belongs in the territory map, not the news queue.
            freshness_hours=_int("freshness_hours", 48, 1, 336),
            sources=list(sources),
        ))
    return found


class SignalStore:
    """Live signals, with expiry handled in the query rather than by a sweep."""

    def __init__(self, path: str | Path = "agenticcore.db"):
        self.path = str(path)
        with closing(self._connect()) as conn:
            conn.execute(SIGNAL_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row(r: sqlite3.Row) -> MarketSignal:
        return MarketSignal(
            id=r["id"], brand_slug=r["brand_slug"], headline=r["headline"],
            kind=r["kind"], what_happened=r["what_happened"] or "",
            why_it_matters=r["why_it_matters"] or "", angle=r["angle"] or "",
            freshness_hours=r["freshness_hours"], urgency=r["urgency"],
            sources=json.loads(r["sources"]) if r["sources"] else [],
            status=r["status"], used_by_draft=r["used_by_draft"],
        )

    def add_all(self, signals: list[MarketSignal]) -> list[MarketSignal]:
        """Store new signals, skipping headlines already being tracked."""

        if not signals:
            return []
        known = {s.headline.lower() for s in self.for_brand(signals[0].brand_slug)}
        fresh, seen = [], set()
        for s in signals:
            key = s.headline.lower()
            if key in known or key in seen:
                continue
            seen.add(key)
            fresh.append(s)

        with closing(self._connect()) as conn:
            for s in fresh:
                conn.execute(
                    """INSERT INTO market_signals
                       (id, brand_slug, kind, headline, what_happened, why_it_matters,
                        angle, freshness_hours, urgency, sources)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (s.id, s.brand_slug, s.kind, s.headline, s.what_happened,
                     s.why_it_matters, s.angle, s.freshness_hours, s.urgency,
                     json.dumps(s.sources)),
                )
            conn.commit()
        return fresh

    def for_brand(self, brand_slug: str) -> list[MarketSignal]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM market_signals WHERE brand_slug = ? ORDER BY detected_at DESC",
                (brand_slug,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def live(self, brand_slug: str) -> list[MarketSignal]:
        """Unused signals still inside their window, most urgent first.

        Ties break toward whatever expires soonest, so a signal is spent
        before it dies rather than sitting behind an equally urgent one with
        days left on it.
        """

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT * FROM market_signals
                   WHERE brand_slug = ? AND status = ?
                     AND datetime(detected_at, '+' || freshness_hours || ' hours')
                         > datetime('now')
                   ORDER BY urgency DESC,
                            datetime(detected_at, '+' || freshness_hours || ' hours') ASC""",
                (brand_slug, STATUS_OPEN),
            ).fetchall()
        return [self._row(r) for r in rows]

    def next_live(self, brand_slug: str) -> Optional[MarketSignal]:
        live = self.live(brand_slug)
        return live[0] if live else None

    def mark_used(self, signal_id: str, draft_id: Optional[str] = None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """UPDATE market_signals SET status = ?, used_by_draft = ?,
                   used_at = datetime('now') WHERE id = ?""",
                (STATUS_USED, draft_id, signal_id),
            )
            conn.commit()

    def expire_stale(self, brand_slug: Optional[str] = None) -> int:
        """Mark signals whose window has closed, so they stop being counted."""

        sql = """UPDATE market_signals SET status = ?
                 WHERE status = ?
                   AND datetime(detected_at, '+' || freshness_hours || ' hours')
                       <= datetime('now')"""
        params: list = [STATUS_EXPIRED, STATUS_OPEN]
        if brand_slug:
            sql += " AND brand_slug = ?"
            params.append(brand_slug)
        with closing(self._connect()) as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount


_SIGNAL_FORMAT = (
    "Reply with nothing but signal blocks in exactly this format:\n\n"
    "### SIGNAL\n"
    "HEADLINE: <the thing that happened, in one line>\n"
    "KIND: <event, trend, or competitor>\n"
    "WHAT: <what actually happened, factually, from your sources>\n"
    "WHY: <why this specific audience cares — not why it is interesting>\n"
    "ANGLE: <what this brand should say about it that others will not>\n"
    "URGENCY: <1-5, 5 meaning post today or lose it>\n"
    "FRESHNESS_HOURS: <how long this stays worth posting about>\n\n"
    "If nothing genuinely newsworthy turned up, reply with the single line "
    "NO SIGNALS FOUND. A quiet week is a real finding. Manufacturing urgency "
    "about a non-event wastes the one slot that jumps the queue."
)

EVENT_SYSTEM = (
    "You monitor a market for things worth reacting to this week. Search "
    "before answering; anything you already knew is by definition not news.\n\n"
    "You are looking for what changed: a launch, a price move, a platform or "
    "policy change, a public failure, a study, a debate the audience is "
    "having right now. Recency is the whole point — something from three "
    "months ago is not a signal even if it is interesting.\n\n"
    "Judge each candidate by whether this brand's audience would actually "
    "change their mind or their plans because of it. Industry noise that "
    "changes nothing for them is not a signal.\n\n"
    "Set FRESHNESS_HOURS honestly. A breaking launch may be worth 24 hours; "
    "a slow-moving shift may be worth a week. If something would still be "
    "worth posting in a month, it is evergreen and does not belong here.\n\n"
    + _SIGNAL_FORMAT
)

COMPETITOR_SYSTEM = (
    "You monitor what a brand's competitors are publishing. Search for their "
    "actual public content and positioning.\n\n"
    "A hard limit you must respect: search shows you what a competitor "
    "PUBLISHED. It does not show how that content performed. You have no "
    "access to their impressions, engagement, or follower growth. Never "
    "state or imply such numbers — inventing a rival's engagement figure is "
    "the same failure as inventing a statistic about yourself.\n\n"
    "What is genuinely useful:\n"
    "- claims they all make, which are therefore worth nothing to repeat\n"
    "- questions they all avoid answering, which are the real openings\n"
    "- a position one of them has taken that this brand should answer\n"
    "- a gap: something this audience needs that none of them covers\n\n"
    "Do not suggest naming or attacking a competitor. The useful move is "
    "taking the better position, not the fight.\n\n"
    + _SIGNAL_FORMAT
)


class SignalAgent:
    """Watches the market and the competition for things worth reacting to."""

    name = "signal_monitor"

    def __init__(self, research_client):
        self.client = research_client

    def _brand_context(self, brand) -> list[str]:
        lines = [
            f"Brand: {brand.name}",
            f"What it does: {getattr(brand, 'description', '') or brand.goal or brand.name}",
            f"Its audience: {brand.audience}",
        ]
        if getattr(brand, "keywords", None):
            lines.append(f"Its space: {', '.join(brand.keywords)}")
        return lines

    def _run(self, system: str, brand, tail: list[str], known: Optional[list[str]],
             max_searches: int) -> tuple[list[MarketSignal], object]:
        prompt = self._brand_context(brand)
        if known:
            prompt += ["", "Already tracked — do not repeat these:",
                       *(f"- {h}" for h in known[:30])]
        prompt += [""] + tail

        result = self.client.research(system, "\n".join(prompt), max_searches=max_searches)
        if "NO SIGNALS FOUND" in result.text.upper():
            return [], result
        return parse_signals(result.text, brand.slug, result.sources), result

    def find_events(self, brand, known: Optional[list[str]] = None,
                    max_searches: int = 6) -> tuple[list[MarketSignal], object]:
        return self._run(
            EVENT_SYSTEM, brand,
            ["Search for what has changed in this market in the last week or two, "
             "and return only what this audience would actually act on."],
            known, max_searches,
        )

    def watch_competitors(self, brand, known: Optional[list[str]] = None,
                          max_searches: int = 6) -> tuple[list[MarketSignal], object]:
        return self._run(
            COMPETITOR_SYSTEM, brand,
            ["Search for what this brand's competitors are publicly saying and "
             "publishing, and return the openings that leaves."],
            known, max_searches,
        )
