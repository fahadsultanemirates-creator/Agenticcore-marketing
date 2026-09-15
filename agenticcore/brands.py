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


# What creative a post carries. At most one image and one video are
# generated per campaign and shared by the pages that want them — a brand
# spanning YouTube and LinkedIn pays for one video and one graphic, not one
# of each per page.
MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_NONE = "none"
MEDIA_CHOICES = frozenset({MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_NONE})

# Local guards that catch a misconfigured brand file before a campaign is
# generated and rejected at publish time. Ayrshare enforces the real rules
# server-side and they shift as the networks change, so keep these
# conservative — only constraints worth failing fast on belong here.
VIDEO_ONLY_PLATFORMS = frozenset({"youtube"})
MEDIA_REQUIRED_PLATFORMS = frozenset({"instagram", "youtube", "tiktok", "pinterest"})

# Ayrshare's platform ids are not always the name you would reach for.
# Mapping the near-misses to the real id turns a silent publish failure into
# a startup error that says what to write instead.
PLATFORM_ID_CORRECTIONS = {
    "x": "twitter",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "facebook_page": "facebook",
    "fb": "facebook",
    "ig": "instagram",
    "yt": "youtube",
    "google_business": "gmb",
    "googlebusiness": "gmb",
}


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
    media: Optional[str] = None  # overrides the brand's default for this page


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
        slug = data["slug"]
        media = cls._validated_media(data.get("media", MEDIA_IMAGE), slug)
        channels = [ChannelTarget(**c) for c in data.get("channels", [])]

        for target in channels:
            where = f"brand {slug!r}, page {target.channel!r}"
            correction = PLATFORM_ID_CORRECTIONS.get(target.channel.lower())
            if correction:
                raise ValueError(
                    f"{where}: Ayrshare calls this platform {correction!r} — "
                    f"use that as the channel id."
                )
            if target.media is not None:
                cls._validated_media(target.media, where)

        profile = cls(
            slug=slug,
            name=data["name"],
            audience=data["audience"],
            goal=data.get("goal", ""),
            tone=data.get("tone", "confident and clear"),
            channels=channels,
            ayrshare_profile_key=data.get("ayrshare_profile_key"),
            telegram_chat_id=data.get("telegram_chat_id"),
            media=media,
        )
        profile._check_platform_media_rules()
        return profile

    @staticmethod
    def _validated_media(value: str, where: str) -> str:
        if value not in MEDIA_CHOICES:
            raise ValueError(
                f"{where}: media must be one of {sorted(MEDIA_CHOICES)}, got {value!r}"
            )
        return value

    def _check_platform_media_rules(self) -> None:
        """Fail on a page whose platform cannot carry what it is configured for.

        Cheaper to catch here than to generate a whole campaign, send it for
        approval, and have Ayrshare reject the tap.
        """

        for target in self.channels:
            resolved = self.media_for(target)
            where = f"brand {self.slug!r}, page {target.channel!r}"
            if target.channel in VIDEO_ONLY_PLATFORMS and resolved != MEDIA_VIDEO:
                raise ValueError(
                    f"{where}: {target.channel} requires a video, but this page "
                    f"resolves to media={resolved!r}. Set \"media\": \"video\" on "
                    f"the page (or on the brand)."
                )
            if target.channel in MEDIA_REQUIRED_PLATFORMS and resolved == MEDIA_NONE:
                raise ValueError(
                    f"{where}: {target.channel} cannot take a text-only post. "
                    f"Set \"media\" to \"image\" or \"video\" on the page."
                )

    def media_for(self, target: ChannelTarget) -> str:
        """What creative this page carries: its own setting, else the brand's."""

        return target.media or self.media

    def required_media_types(self) -> set[str]:
        """The distinct assets one campaign for this brand has to produce."""

        return {self.media_for(t) for t in self.channels} - {MEDIA_NONE}

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
