"""Multi-project (multi-brand) configuration.

You run 5-6 of your own projects through one framework instance by giving
each a small JSON profile under ``brands/`` describing its audience, tone,
and which Ayrshare profile/platform each of its social pages maps to.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# What creative a brand's posts carry. One asset is generated per campaign and
# shared across that brand's pages, the way a single graphic gets reused
# across a Facebook page and an Instagram account.
MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_NONE = "none"
MEDIA_CHOICES = frozenset({MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_NONE})


@dataclass
class ChannelTarget:
    """One connected page/account belonging to a brand, ready to publish to.

    ``channel`` is the Ayrshare platform id ("facebook", "instagram",
    "linkedin", "twitter", "tiktok", "youtube", "pinterest", ...) for a page
    you've already linked inside Ayrshare's dashboard under this brand's
    profile.
    """

    channel: str
    label: str = ""  # human-readable name shown in Telegram, e.g. "Acme FB Page"


@dataclass
class BrandProfile:
    """Everything the framework needs to run campaigns for one project.

    ``ayrshare_profile_key`` is this project's Ayrshare "Profile-Key" — the
    single key that groups all of that project's linked social pages. On
    Ayrshare's single-profile plans this can be left null; every brand then
    posts through the one default profile on your API key.
    """

    slug: str
    name: str
    audience: str
    goal: str = ""
    tone: str = "confident and clear"
    channels: List[ChannelTarget] = field(default_factory=list)
    ayrshare_profile_key: Optional[str] = None
    telegram_chat_id: Optional[str] = None  # overrides the global default chat
    media: str = "image"  # one of MEDIA_CHOICES; see below

    @classmethod
    def from_dict(cls, data: dict) -> "BrandProfile":
        channels = [ChannelTarget(**c) for c in data.get("channels", [])]
        media = data.get("media", "image")
        if media not in MEDIA_CHOICES:
            raise ValueError(
                f"brand {data.get('slug')!r}: media must be one of "
                f"{sorted(MEDIA_CHOICES)}, got {media!r}"
            )
        return cls(
            slug=data["slug"],
            name=data["name"],
            audience=data["audience"],
            goal=data.get("goal", ""),
            tone=data.get("tone", "confident and clear"),
            channels=channels,
            ayrshare_profile_key=data.get("ayrshare_profile_key"),
            telegram_chat_id=data.get("telegram_chat_id"),
            media=media,
        )

    def has_channel(self, channel_id: str) -> bool:
        return any(target.channel == channel_id for target in self.channels)


class BrandRegistry:
    """Loads every ``*.json`` profile in a directory (default: ``brands/``)."""

    def __init__(self, directory: str | Path = "brands"):
        self.directory = Path(directory)
        self._brands: Dict[str, BrandProfile] = {}
        self.reload()

    def reload(self) -> None:
        self._brands.clear()
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.json")):
            data = json.loads(path.read_text())
            brand = BrandProfile.from_dict(data)
            self._brands[brand.slug] = brand

    def get(self, slug: str) -> BrandProfile:
        return self._brands[slug]

    def all(self) -> List[BrandProfile]:
        return list(self._brands.values())
