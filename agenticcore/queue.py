"""Persistent queue of post drafts moving through approval to publication.

States: draft -> pending_approval -> approved | rejected -> published
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    brand_slug TEXT NOT NULL,
    channel TEXT NOT NULL,
    caption TEXT NOT NULL,
    image_path TEXT,
    video_path TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    telegram_chat_id TEXT,
    telegram_message_id INTEGER,
    published_post_id TEXT,
    reach_score INTEGER,
    first_comment TEXT,
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# Every column a caller may hand to DraftStore.update. `id` and `created_at`
# are deliberately absent: one identifies the row, the other is set once.
UPDATABLE_COLUMNS = frozenset(
    {
        "brand_slug",
        "channel",
        "caption",
        "image_path",
        "video_path",
        "status",
        "telegram_chat_id",
        "telegram_message_id",
        "published_post_id",
        "reach_score",
        "first_comment",
        "published_at",
    }
)


@dataclass
class PostDraft:
    id: str
    brand_slug: str
    channel: str
    caption: str
    image_path: Optional[str] = None
    video_path: Optional[str] = None
    status: str = "draft"
    telegram_chat_id: Optional[str] = None
    telegram_message_id: Optional[int] = None
    published_post_id: Optional[str] = None
    reach_score: Optional[int] = None
    first_comment: Optional[str] = None
    published_at: Optional[str] = None

    def telegram_caption(self, brand_name: str, channel_label: str) -> str:
        return f"[{brand_name} -> {channel_label}]\n\n{self.caption}"

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "PostDraft":
        return cls(
            id=row["id"],
            brand_slug=row["brand_slug"],
            channel=row["channel"],
            caption=row["caption"],
            image_path=row["image_path"],
            video_path=row["video_path"],
            status=row["status"],
            telegram_chat_id=row["telegram_chat_id"],
            telegram_message_id=row["telegram_message_id"],
            published_post_id=row["published_post_id"],
            reach_score=row["reach_score"],
            first_comment=row["first_comment"],
            published_at=row["published_at"],
        )


class DraftStore:
    """Small SQLite-backed store so drafts survive a bot restart."""

    def __init__(self, path: str | Path = "agenticcore.db"):
        self.path = str(path)
        with closing(self._connect()) as conn:
            conn.execute(SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after a database was first created.

        CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a
        store created by an earlier version keeps its old shape until the
        missing columns are added explicitly.
        """

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(drafts)")}
        for column, ddl in (("reach_score", "INTEGER"), ("first_comment", "TEXT"),
                            ("published_at", "TEXT")):
            if column not in existing:
                conn.execute(f"ALTER TABLE drafts ADD COLUMN {column} {ddl}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def create(
        self,
        brand_slug: str,
        channel: str,
        caption: str,
        image_path: Optional[str] = None,
        video_path: Optional[str] = None,
        status: str = "draft",
    ) -> PostDraft:
        draft = PostDraft(
            id=str(uuid.uuid4()),
            brand_slug=brand_slug,
            channel=channel,
            caption=caption,
            image_path=image_path,
            video_path=video_path,
            status=status,
        )
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO drafts
                   (id, brand_slug, channel, caption, image_path, video_path, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (draft.id, draft.brand_slug, draft.channel, draft.caption,
                 draft.image_path, draft.video_path, draft.status),
            )
            conn.commit()
        return draft

    def get(self, draft_id: str) -> Optional[PostDraft]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return PostDraft._from_row(row) if row else None

    def list_by_status(self, status: str) -> list[PostDraft]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        return [PostDraft._from_row(row) for row in rows]

    def list_for_brand(
        self,
        brand_slug: str,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list["PostDraft"]:
        """This brand's drafts, newest first, optionally filtered by status."""

        sql = "SELECT * FROM drafts WHERE brand_slug = ?"
        params: list = [brand_slug]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [PostDraft._from_row(row) for row in rows]

    def update(self, draft_id: str, **fields) -> None:
        if not fields:
            return
        unknown = set(fields) - UPDATABLE_COLUMNS
        if unknown:
            # Column names can't be bound as parameters, so they're
            # interpolated into the statement — check them against the schema
            # rather than trusting every caller to pass a real column.
            raise ValueError(f"Not updatable columns: {sorted(unknown)}")
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [draft_id]
        with closing(self._connect()) as conn:
            conn.execute(
                f"UPDATE drafts SET {columns}, updated_at = datetime('now') WHERE id = ?",
                values,
            )
            conn.commit()
