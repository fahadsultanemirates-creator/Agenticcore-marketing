"""What the framework knows about each website it writes for.

A post is only as good as what the writer knows about the business. Given a
one-line brand description, agents reach for generic claims because they
have nothing specific to say. Given the actual site — the services, the real
prices, the CTAs, the words the business uses about itself — they can write
ten different posts that are all true.

The profile is read from the live site via Claude's server-side fetch, so it
reflects what the site actually says today rather than what anyone
remembers it saying. Re-read it whenever the site changes.

One rule runs through the extraction: **record only what is on the page.**
An invented price or a fabricated client is worse than a missing field,
because a missing field makes the writer work around the gap while an
invented one gets published.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agenticcore.research.parsing import parse_fields, split_blocks

WEBSITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS website_profiles (
    brand_slug TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    summary TEXT,
    sells TEXT,
    audience TEXT,
    offers TEXT,
    prices TEXT,
    cta TEXT,
    proof TEXT,
    tone TEXT,
    topics TEXT,
    pages_read TEXT,
    read_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class WebsiteProfile:
    """Everything the writing agents should know about one site."""

    brand_slug: str
    url: str
    summary: str = ""
    sells: str = ""
    audience: str = ""
    offers: str = ""
    prices: str = ""
    cta: str = ""
    proof: str = ""
    tone: str = ""
    #: Post angles the reader spotted in the site's own material.
    topics: list[str] = field(default_factory=list)
    pages_read: list[str] = field(default_factory=list)
    read_at: str = ""

    def as_brief(self) -> str:
        """The profile as prompt text for the writing agents."""

        parts = [f"What you know about {self.url}, read from the live site:"]
        for label, value in (
            ("What it sells", self.sells),
            ("Who it is for", self.audience),
            ("Offers and packages", self.offers),
            ("Prices actually published", self.prices),
            ("How a visitor converts", self.cta),
            ("Proof it can cite", self.proof),
            ("How it talks about itself", self.tone),
        ):
            if value:
                parts.append(f"- {label}: {value}")
        if not self.proof:
            parts.append(
                "- Proof it can cite: NONE. The site names no clients, results or "
                "years. Write from reasoning and specifics, never from credibility "
                "the business has not earned yet."
            )
        parts.append(
            "Everything you write must be supported by the above. Do not invent "
            "a price, a feature, a client or a result that is not there."
        )
        return "\n".join(parts)


PROFILE_LABELS = ("SUMMARY", "SELLS", "AUDIENCE", "OFFERS", "PRICES",
                  "CTA", "PROOF", "TONE", "TOPICS")

READER_SYSTEM = (
    "You read a company's website and write down what is actually there, so "
    "a content team can work from facts instead of guesses.\n\n"
    "Read the page you are given, then follow and read its most informative "
    "internal links — services, pricing, packages, about, and any page "
    "describing a specific offer. Stop when you have the picture.\n\n"
    "The one rule that matters: record ONLY what the pages say. If a price "
    "is not published, write 'not published'. If there are no clients, case "
    "studies or results anywhere, write 'none stated'. A missing field makes "
    "a writer work around the gap; an invented one gets published as a false "
    "claim. Never fill a gap with something plausible.\n\n"
    "Quote the site's own phrasing where it is distinctive — that is what "
    "lets content sound like the business rather than like every competitor.\n\n"
    "Reply in exactly this format and nothing else:\n\n"
    "SUMMARY: <what this business is, in two or three sentences>\n"
    "SELLS: <the concrete services or products, listed>\n"
    "AUDIENCE: <who the site addresses, in its own words where possible>\n"
    "OFFERS: <named packages or bundles and what each includes>\n"
    "PRICES: <every figure actually published, with what it buys; else 'not published'>\n"
    "CTA: <the primary action and how a visitor takes it>\n"
    "PROOF: <named clients, results, stats, years; else 'none stated'>\n"
    "TONE: <how the site talks — the recurring phrases and the register>\n"
    "TOPICS: <8-12 post angles this site's own material would support, "
    "separated by ' | '>"
)


class WebsiteReader:
    """Reads a live site into a profile the writing agents can use."""

    def __init__(self, research_client):
        self.client = research_client

    def read(self, brand_slug: str, url: str, max_fetches: int = 8) -> tuple[WebsiteProfile, object]:
        result = self.client.read_pages(
            READER_SYSTEM,
            f"Read {url} and the most informative pages linked from it, then "
            f"report what is actually there.",
            max_fetches=max_fetches,
        )
        return parse_profile(result.text, brand_slug, url, result.sources), result


def parse_profile(text: str, brand_slug: str, url: str,
                  pages_read: Optional[list[str]] = None) -> WebsiteProfile:
    """Read a profile out of the reader's reply, tolerating missing fields."""

    fields = parse_fields(text, PROFILE_LABELS)
    raw_topics = fields.get("topics", "")
    topics = [t.strip() for t in raw_topics.split("|") if len(t.strip()) > 8]

    def clean(key: str) -> str:
        value = fields.get(key, "")
        # "not published" and "none stated" are real answers worth keeping,
        # so they are not treated as empty.
        return value

    return WebsiteProfile(
        brand_slug=brand_slug, url=url,
        summary=clean("summary"), sells=clean("sells"),
        audience=clean("audience"), offers=clean("offers"),
        prices=clean("prices"), cta=clean("cta"),
        tone=clean("tone"),
        # A site that states no proof must read as empty, not as the words
        # "none stated" — as_brief keys its warning off this being falsy.
        proof="" if clean("proof").lower().startswith(("none", "no ")) else clean("proof"),
        topics=topics,
        pages_read=list(pages_read or []),
    )


class WebsiteStore:
    """One profile per brand, replaced whenever the site is re-read."""

    def __init__(self, path: str | Path = "agenticcore.db"):
        self.path = str(path)
        with closing(self._connect()) as conn:
            conn.execute(WEBSITE_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, profile: WebsiteProfile) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO website_profiles
                   (brand_slug, url, summary, sells, audience, offers, prices,
                    cta, proof, tone, topics, pages_read, read_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(brand_slug) DO UPDATE SET
                     url=excluded.url, summary=excluded.summary, sells=excluded.sells,
                     audience=excluded.audience, offers=excluded.offers,
                     prices=excluded.prices, cta=excluded.cta, proof=excluded.proof,
                     tone=excluded.tone, topics=excluded.topics,
                     pages_read=excluded.pages_read, read_at=datetime('now')""",
                (profile.brand_slug, profile.url, profile.summary, profile.sells,
                 profile.audience, profile.offers, profile.prices, profile.cta,
                 profile.proof, profile.tone, json.dumps(profile.topics),
                 json.dumps(profile.pages_read)),
            )
            conn.commit()

    def get(self, brand_slug: str) -> Optional[WebsiteProfile]:
        with closing(self._connect()) as conn:
            r = conn.execute(
                "SELECT * FROM website_profiles WHERE brand_slug = ?", (brand_slug,)
            ).fetchone()
        if not r:
            return None
        return WebsiteProfile(
            brand_slug=r["brand_slug"], url=r["url"], summary=r["summary"] or "",
            sells=r["sells"] or "", audience=r["audience"] or "",
            offers=r["offers"] or "", prices=r["prices"] or "", cta=r["cta"] or "",
            proof=r["proof"] or "", tone=r["tone"] or "",
            topics=json.loads(r["topics"]) if r["topics"] else [],
            pages_read=json.loads(r["pages_read"]) if r["pages_read"] else [],
            read_at=r["read_at"],
        )
