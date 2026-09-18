"""Content opportunities: specific things worth posting about, with evidence.

This is the difference between a publisher and a marketer. A publisher is
handed a brand description and invents a topic. A marketer finds out what
the audience is actually asking, then answers it.

An opportunity is one researched topic — the real question people ask, the
evidence it matters, and the angle this brand should take. They are stored
and consumed: once a campaign uses one it is marked, so the next campaign
picks a different one and the brand works through its territory instead of
circling the same three ideas.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agenticcore.research.parsing import parse_fields, split_blocks

OPPORTUNITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    brand_slug TEXT NOT NULL,
    query TEXT NOT NULL,
    evidence TEXT,
    angle TEXT,
    suggested_format TEXT,
    sources TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    used_by_draft TEXT,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    used_at TEXT
);
"""

STATUS_OPEN = "open"
STATUS_USED = "used"
STATUS_DROPPED = "dropped"


@dataclass
class ContentOpportunity:
    """One researched thing worth saying, and why."""

    id: str
    brand_slug: str
    query: str
    evidence: str = ""
    angle: str = ""
    suggested_format: str = ""
    sources: list[str] = field(default_factory=list)
    status: str = STATUS_OPEN
    used_by_draft: Optional[str] = None

    def as_brief(self) -> str:
        """The opportunity as prompt text for the writing agents."""

        lines = [f"The question this post must answer: {self.query}"]
        if self.evidence:
            lines.append(f"Why it matters: {self.evidence}")
        if self.angle:
            lines.append(f"The angle to take: {self.angle}")
        if self.sources:
            lines.append(
                "Researched from: " + ", ".join(self.sources[:5]) +
                ". Use only what those support — do not embellish beyond them."
            )
        return "\n".join(lines)


OPPORTUNITY_LABELS = ("QUERY", "EVIDENCE", "ANGLE", "FORMAT")


def parse_opportunities(text: str, brand_slug: str, sources: list[str]) -> list[ContentOpportunity]:
    """Read opportunities out of the research agent's reply.

    Tolerant by design: a missing EVIDENCE or ANGLE yields an opportunity
    with those blank rather than dropping a real finding, and a reply with no
    QUERY at all yields nothing rather than raising.
    """

    found = []
    for block in split_blocks(text, "QUERY"):
        fields = parse_fields(block, OPPORTUNITY_LABELS)
        query = fields.get("query", "")
        if len(query) < 8:
            continue
        found.append(ContentOpportunity(
            id=str(uuid.uuid4()),
            brand_slug=brand_slug,
            query=query,
            evidence=fields.get("evidence", ""),
            angle=fields.get("angle", ""),
            suggested_format=fields.get("format", "").lower(),
            sources=list(sources),
        ))
    return found


class OpportunityStore:
    """Researched topics, and which ones have been spent."""

    def __init__(self, path: str | Path = "agenticcore.db"):
        self.path = str(path)
        with closing(self._connect()) as conn:
            conn.execute(OPPORTUNITY_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row(r: sqlite3.Row) -> ContentOpportunity:
        return ContentOpportunity(
            id=r["id"], brand_slug=r["brand_slug"], query=r["query"],
            evidence=r["evidence"] or "", angle=r["angle"] or "",
            suggested_format=r["suggested_format"] or "",
            sources=json.loads(r["sources"]) if r["sources"] else [],
            status=r["status"], used_by_draft=r["used_by_draft"],
        )

    def add_all(self, opportunities: list[ContentOpportunity]) -> list[ContentOpportunity]:
        """Store new opportunities, skipping ones already known for the brand.

        Weekly research re-surfaces the same evergreen questions; without
        this the queue fills with duplicates and the brand keeps answering
        the same thing.
        """

        known = {o.query.lower() for o in self.for_brand(
            opportunities[0].brand_slug) } if opportunities else set()
        fresh = [o for o in opportunities if o.query.lower() not in known]
        with closing(self._connect()) as conn:
            for o in fresh:
                conn.execute(
                    """INSERT INTO opportunities
                       (id, brand_slug, query, evidence, angle, suggested_format, sources, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (o.id, o.brand_slug, o.query, o.evidence, o.angle,
                     o.suggested_format, json.dumps(o.sources), o.status),
                )
            conn.commit()
        return fresh

    def for_brand(self, brand_slug: str, status: Optional[str] = None) -> list[ContentOpportunity]:
        sql = "SELECT * FROM opportunities WHERE brand_slug = ?"
        params: list = [brand_slug]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY discovered_at"
        with closing(self._connect()) as conn:
            return [self._row(r) for r in conn.execute(sql, params).fetchall()]

    def next_open(self, brand_slug: str) -> Optional[ContentOpportunity]:
        """The oldest unused opportunity — work the territory in order."""

        open_ones = self.for_brand(brand_slug, status=STATUS_OPEN)
        return open_ones[0] if open_ones else None

    def mark_used(self, opportunity_id: str, draft_id: Optional[str] = None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """UPDATE opportunities
                   SET status = ?, used_by_draft = ?, used_at = datetime('now')
                   WHERE id = ?""",
                (STATUS_USED, draft_id, opportunity_id),
            )
            conn.commit()
