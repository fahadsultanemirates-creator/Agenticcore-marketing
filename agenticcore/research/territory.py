"""The query space a brand should own, and how much of it it has covered.

Demand research answers "what should we post this week" — timely, evidenced,
consumed one at a time. Territory is the other axis: the whole map of
questions this brand could own, worked through systematically over months.

The reason to bother is that the two kinds of reach behave differently. Feed
reach peaks in hours and is gone in days. Search reach on TikTok, YouTube,
Instagram and Pinterest compounds — a post answering a question people keep
asking keeps being found long after the feed forgot it. Covering a territory
deliberately is how you accumulate the second kind instead of hoping for it.

A deliberate omission: there is no search-volume field. Volume needs a
keyword tool this framework is not connected to, and a number the model
produced would be indistinguishable from one it looked up. ``priority`` is
an explicit judgment with a stated ``rationale`` instead, which is honest
about what it is.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agenticcore.research.parsing import parse_fields, split_blocks

TERRITORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_targets (
    id TEXT PRIMARY KEY,
    brand_slug TEXT NOT NULL,
    term TEXT NOT NULL,
    cluster TEXT NOT NULL DEFAULT 'general',
    intent TEXT NOT NULL DEFAULT 'informational',
    stage TEXT NOT NULL DEFAULT 'awareness',
    rationale TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    sources TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS keyword_coverage (
    draft_id TEXT NOT NULL,
    keyword_id TEXT NOT NULL,
    brand_slug TEXT NOT NULL,
    covered_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (draft_id, keyword_id)
);
"""

INTENTS = ("informational", "commercial", "transactional")
STAGES = ("awareness", "consideration", "decision")


@dataclass
class KeywordTarget:
    """One query the brand wants to be found for."""

    id: str
    brand_slug: str
    term: str
    cluster: str = "general"
    intent: str = "informational"
    stage: str = "awareness"
    rationale: str = ""
    priority: int = 3
    sources: list[str] = field(default_factory=list)
    times_covered: int = 0

    @property
    def is_covered(self) -> bool:
        return self.times_covered > 0


TERM_LABELS = ("TERM", "CLUSTER", "INTENT", "STAGE", "PRIORITY", "WHY")


def _one_of(value: Optional[str], allowed: tuple[str, ...], default: str) -> str:
    cleaned = (value or "").strip().lower()
    return cleaned if cleaned in allowed else default


def parse_territory(text: str, brand_slug: str, sources: list[str]) -> list[KeywordTarget]:
    """Read keyword targets out of the territory agent's reply."""

    targets = []
    for block in split_blocks(text, "TERM"):
        fields = parse_fields(block, TERM_LABELS)
        term = fields.get("term", "").lower()
        if len(term) < 5:
            continue
        try:
            priority = int(re.sub(r"\D", "", fields.get("priority", "")) or 3)
        except ValueError:
            priority = 3
        targets.append(KeywordTarget(
            id=str(uuid.uuid4()),
            brand_slug=brand_slug,
            term=term,
            cluster=(fields.get("cluster") or "general").lower(),
            intent=_one_of(fields.get("intent"), INTENTS, "informational"),
            stage=_one_of(fields.get("stage"), STAGES, "awareness"),
            rationale=fields.get("why", ""),
            priority=max(1, min(5, priority)),
            sources=list(sources),
        ))
    return targets


class TerritoryStore:
    """The map, and which of it has been published against."""

    def __init__(self, path: str | Path = "agenticcore.db"):
        self.path = str(path)
        with closing(self._connect()) as conn:
            conn.executescript(TERRITORY_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_all(self, targets: list[KeywordTarget]) -> list[KeywordTarget]:
        """Store new terms, skipping ones the brand already has."""

        if not targets:
            return []
        known = {t.term for t in self.for_brand(targets[0].brand_slug)}
        fresh, seen = [], set()
        for t in targets:
            if t.term in known or t.term in seen:
                continue
            seen.add(t.term)
            fresh.append(t)

        with closing(self._connect()) as conn:
            for t in fresh:
                conn.execute(
                    """INSERT INTO keyword_targets
                       (id, brand_slug, term, cluster, intent, stage, rationale,
                        priority, sources)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (t.id, t.brand_slug, t.term, t.cluster, t.intent, t.stage,
                     t.rationale, t.priority, json.dumps(t.sources)),
                )
            conn.commit()
        return fresh

    def for_brand(self, brand_slug: str) -> list[KeywordTarget]:
        """Every target, with how many published posts have covered it."""

        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT t.*, COUNT(c.draft_id) AS times_covered
                   FROM keyword_targets t
                   LEFT JOIN keyword_coverage c ON c.keyword_id = t.id
                   WHERE t.brand_slug = ?
                   GROUP BY t.id
                   ORDER BY t.priority DESC, t.created_at""",
                (brand_slug,),
            ).fetchall()
        return [
            KeywordTarget(
                id=r["id"], brand_slug=r["brand_slug"], term=r["term"],
                cluster=r["cluster"], intent=r["intent"], stage=r["stage"],
                rationale=r["rationale"] or "", priority=r["priority"],
                sources=json.loads(r["sources"]) if r["sources"] else [],
                times_covered=r["times_covered"],
            )
            for r in rows
        ]

    def next_gap(self, brand_slug: str) -> Optional[KeywordTarget]:
        """The highest-priority term nothing has been published against yet.

        Falls back to the least-covered term once the map is fully covered,
        so a brand that has worked through its territory deepens it rather
        than stopping.
        """

        targets = self.for_brand(brand_slug)
        if not targets:
            return None
        uncovered = [t for t in targets if not t.is_covered]
        if uncovered:
            return uncovered[0]  # already sorted by priority
        return min(targets, key=lambda t: (t.times_covered, -t.priority))

    def record_coverage(self, draft_id: str, keyword_id: str, brand_slug: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO keyword_coverage
                   (draft_id, keyword_id, brand_slug) VALUES (?, ?, ?)""",
                (draft_id, keyword_id, brand_slug),
            )
            conn.commit()

    def coverage_report(self, brand_slug: str) -> str:
        """Human-readable map of what's covered and what isn't, by cluster."""

        targets = self.for_brand(brand_slug)
        if not targets:
            return "No territory mapped yet. Run --territory to build one."

        by_cluster: dict[str, list[KeywordTarget]] = defaultdict(list)
        for t in targets:
            by_cluster[t.cluster].append(t)

        covered = sum(1 for t in targets if t.is_covered)
        lines = [
            f"Territory for {brand_slug}: {covered}/{len(targets)} terms covered "
            f"across {len(by_cluster)} cluster(s)",
            "",
        ]
        for cluster in sorted(by_cluster, key=lambda c: -len(by_cluster[c])):
            items = by_cluster[cluster]
            done = sum(1 for t in items if t.is_covered)
            lines.append(f"{cluster} — {done}/{len(items)}")
            for t in sorted(items, key=lambda t: (t.is_covered, -t.priority)):
                mark = "x" * t.times_covered if t.is_covered else " "
                lines.append(f"  [{mark or ' '}] p{t.priority} {t.intent[:4]}  {t.term}")
            lines.append("")
        return "\n".join(lines).rstrip()


TERRITORY_SYSTEM = (
    "You are a search strategist mapping the territory a brand should own in "
    "in-platform search — TikTok, YouTube, Instagram and Pinterest search, "
    "not just Google. Search your way to the answer; do not work from "
    "assumption.\n\n"
    "You are building a map, not a week's ideas. Cover the whole space a "
    "buyer moves through: the problem they have before they know your "
    "category exists, the comparisons they make while deciding, the "
    "objections and trust questions they ask before buying, and the "
    "practical questions they ask right at the decision.\n\n"
    "Rules that matter more than coverage:\n"
    "- Terms must be phrased the way a person types or speaks them into a "
    "search box. 'ai agent vs zapier' is a real query; 'Leveraging Agentic "
    "Workflows' is a blog title nobody searches.\n"
    "- NEVER state or imply a search volume, difficulty score, CPC, or "
    "traffic estimate. You have no keyword tool and a number you produced "
    "would be indistinguishable from one you looked up. PRIORITY is your "
    "own judgment, and WHY must say what it rests on — evidence you found, "
    "buyer intent, or a gap in what competitors answer.\n"
    "- Prefer terms where existing answers are thin, evasive, or "
    "vendor-serving. A question everyone already answers well is not "
    "territory worth taking.\n\n"
    "Reply with nothing but term blocks in exactly this format:\n\n"
    "### TERM\n"
    "TERM: <the query, lowercase, as a person would say it>\n"
    "CLUSTER: <short group name, reused across related terms>\n"
    "INTENT: <informational, commercial, or transactional>\n"
    "STAGE: <awareness, consideration, or decision>\n"
    "PRIORITY: <1-5, 5 being most worth owning>\n"
    "WHY: <what your priority rests on — one sentence>"
)


class TerritoryAgent:
    """Researches the full query space a brand should work through."""

    name = "territory_mapper"

    def __init__(self, research_client):
        self.client = research_client

    def map_territory(
        self,
        brand,
        how_many: int = 30,
        existing_terms: Optional[list[str]] = None,
        max_searches: int = 8,
    ) -> tuple[list[KeywordTarget], object]:
        prompt = [
            f"Brand: {brand.name}",
            f"What it does: {getattr(brand, 'description', '') or brand.goal or brand.name}",
            f"Target audience: {brand.audience}",
        ]
        if brand.goal:
            prompt.append(f"What its content should drive: {brand.goal}")
        if getattr(brand, "keywords", None):
            prompt.append(f"Seed terms it already wants: {', '.join(brand.keywords)}")

        if existing_terms:
            prompt += [
                "",
                "Already mapped — do not repeat these or near-duplicates:",
                *(f"- {t}" for t in existing_terms[:120]),
                "",
                "Extend the map into clusters that are thin or missing.",
            ]

        prompt += [
            "",
            f"Search now, then return up to {how_many} terms spread across "
            f"several clusters and all three funnel stages.",
        ]

        result = self.client.research(
            TERRITORY_SYSTEM, "\n".join(prompt), max_searches=max_searches
        )
        return parse_territory(result.text, brand.slug, result.sources), result


def cluster_performance(targets: list[KeywordTarget], metrics_by_draft: dict,
                        coverage: list[tuple[str, str]]) -> str:
    """Which clusters actually earn engagement, once results exist.

    Cluster-level signal is sturdier than per-post signal: one post doing
    well is noise, a cluster of eight consistently outperforming is a real
    instruction about what this audience wants.
    """

    by_id = {t.id: t for t in targets}
    rates: dict[str, list[float]] = defaultdict(list)
    for draft_id, keyword_id in coverage:
        target, metric = by_id.get(keyword_id), metrics_by_draft.get(draft_id)
        if target and metric and metric.impressions:
            rates[target.cluster].append(metric.engagement_rate)

    scored = [(c, sum(v) / len(v), len(v)) for c, v in rates.items() if v]
    if not scored:
        return ""

    scored.sort(key=lambda row: -row[1])
    lines = ["Engagement rate by territory cluster (measured posts only):"]
    for cluster, rate, n in scored:
        lines.append(f"  {cluster}: {rate * 100:.1f}% across {n} post(s)")
    return "\n".join(lines)
