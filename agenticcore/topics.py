"""What each site has already been posted about, so it is not said twice.

A topic is spent the moment something is made from it, and stays spent until
you say otherwise. That rule is the whole module, and it is kept separate
from the draft queue because it is its own concern: a video has no caption
and no channel, but it uses up a topic exactly as a post does. Recording
this on drafts left video topics unrecorded, and every video batch came back
on the same subject.

Repetition is allowed only when asked for — either by naming a topic
outright, which always overrides, or by clearing the ledger for that site.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS covered_topics (
    brand_slug TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    topic TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'post',
    source TEXT,
    covered_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (brand_slug, topic_key)
);
"""


def topic_key(topic: str) -> str:
    """A comparable form, so near-identical topics count as the same one."""

    return " ".join((topic or "").lower().split())[:80]


@dataclass
class CoveredTopic:
    brand_slug: str
    topic: str
    kind: str = "post"
    source: str = ""
    covered_at: str = ""


class TopicLedger:
    """One row per topic a site has been written about."""

    def __init__(self, path: str | Path = "agenticcore.db"):
        self.path = str(path)
        with closing(self._connect()) as conn:
            conn.execute(LEDGER_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def mark(self, brand_slug: str, topic: str, kind: str = "post",
             source: str = "") -> bool:
        """Record a topic as spent. Returns False for a blank or repeat."""

        key = topic_key(topic)
        if not key:
            return False
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO covered_topics
                   (brand_slug, topic_key, topic, kind, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (brand_slug, key, topic, kind, source),
            )
            conn.commit()
            return cursor.rowcount > 0

    def used_keys(self, brand_slug: str) -> set[str]:
        with closing(self._connect()) as conn:
            return {
                r["topic_key"] for r in conn.execute(
                    "SELECT topic_key FROM covered_topics WHERE brand_slug = ?",
                    (brand_slug,),
                )
            }

    def covered(self, brand_slug: str) -> list[CoveredTopic]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT * FROM covered_topics WHERE brand_slug = ?
                   ORDER BY covered_at DESC""",
                (brand_slug,),
            ).fetchall()
        return [
            CoveredTopic(r["brand_slug"], r["topic"], r["kind"],
                         r["source"] or "", r["covered_at"])
            for r in rows
        ]

    def reset(self, brand_slug: Optional[str] = None) -> int:
        """Free every topic again — the "until asked for" half of the rule."""

        sql = "DELETE FROM covered_topics"
        params: tuple = ()
        if brand_slug:
            sql += " WHERE brand_slug = ?"
            params = (brand_slug,)
        with closing(self._connect()) as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    def release(self, brand_slug: str, topic: str) -> bool:
        """Free one topic, for when a batch was discarded unused."""

        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "DELETE FROM covered_topics WHERE brand_slug = ? AND topic_key = ?",
                (brand_slug, topic_key(topic)),
            )
            conn.commit()
            return cursor.rowcount > 0
