"""What actually drives reach, per platform, as data the agents can read.

The framework's job is not to fill pages — it is to get seen. These are the
mechanics that decide that, kept in one editable module because they shift
as platforms change. Sourced from the 2026 algorithm reporting (LinkedIn
Engineering on dwell time; the ~60% outbound-link reach penalty on LinkedIn
and Facebook's on-platform suppression; saves and shares outweighing likes;
hashtags demoted from a ranking lever to a weak enhancer) — update here
rather than in any prompt.

The single most consequential field is ``discovery``, because it decides
whether good content can reach anyone at all:

``DISCOVERY_ALGORITHMIC``
    The platform shows content to non-followers on its merits — TikTok's
    For You, Reels, Shorts. A brand-new account with no audience can get
    real reach here. This is where a launching brand should spend.

``DISCOVERY_GRAPH``
    Distribution starts from your follower graph — LinkedIn pages, Facebook
    pages, X. A zero-follower page posting excellent content reaches
    approximately nobody, because there is no one for the first wave to
    reach. Content quality only compounds once an audience exists.

``DISCOVERY_BROADCAST``
    No discovery surface at all — Telegram and WhatsApp channels. Reach
    equals subscriber count, exactly. No writing choice changes it; the
    only lever is driving subscribers from somewhere else. Excellent for
    retention, near-useless for growth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

DISCOVERY_ALGORITHMIC = "algorithmic"
DISCOVERY_GRAPH = "graph"
DISCOVERY_BROADCAST = "broadcast"

# How a link should be handled to avoid the reach penalty.
LINK_IN_BODY_OK = "body_ok"          # no measured penalty
LINK_FIRST_COMMENT = "first_comment"  # keep the body clean, link in a reply
LINK_NOT_CLICKABLE = "not_clickable"  # platform ignores links in copy entirely


@dataclass
class PlatformReach:
    """The reach-relevant shape of one platform."""

    platform: str
    discovery: str
    #: Characters visible before the feed truncates with a "see more". The
    #: hook has to land inside this or the post is never opened.
    hook_chars: int
    #: Where the body length stops helping. Not a hard limit.
    body_target_chars: int
    link_policy: str
    hashtag_range: tuple[int, int]
    #: Format that currently reaches furthest here.
    best_format: str
    #: Whether in-platform search is a meaningful discovery route, which
    #: makes keyword placement worth more than hashtags.
    search_matters: bool = False
    notes: str = ""

    @property
    def new_account_can_reach(self) -> bool:
        """Can content alone reach non-followers from a standing start?"""

        return self.discovery == DISCOVERY_ALGORITHMIC


REACH_RULES: dict[str, PlatformReach] = {
    "tiktok": PlatformReach(
        platform="tiktok", discovery=DISCOVERY_ALGORITHMIC,
        hook_chars=90, body_target_chars=300,
        link_policy=LINK_NOT_CLICKABLE, hashtag_range=(3, 5),
        best_format="vertical video", search_matters=True,
        notes="First 2 seconds decide. Completion rate is the dominant signal. "
              "Say the keyword out loud and put it on screen — TikTok search is "
              "a real discovery route and it compounds long after the feed moves on.",
    ),
    "instagram": PlatformReach(
        platform="instagram", discovery=DISCOVERY_ALGORITHMIC,
        hook_chars=125, body_target_chars=800,
        link_policy=LINK_NOT_CLICKABLE, hashtag_range=(3, 5),
        best_format="reel", search_matters=True,
        notes="Reels reach non-followers; static feed posts largely do not. "
              "Sends and saves outweigh likes. Keywords in the caption feed "
              "in-app search.",
    ),
    "youtube": PlatformReach(
        platform="youtube", discovery=DISCOVERY_ALGORITHMIC,
        hook_chars=100, body_target_chars=5000,
        link_policy=LINK_IN_BODY_OK, hashtag_range=(2, 3),
        best_format="short (vertical video)", search_matters=True,
        notes="Both a feed and the second-largest search engine. Shorts carry "
              "new channels; keyword-led titles keep earning views for months.",
    ),
    "linkedin": PlatformReach(
        platform="linkedin", discovery=DISCOVERY_GRAPH,
        hook_chars=200, body_target_chars=1500,
        link_policy=LINK_FIRST_COMMENT, hashtag_range=(1, 3),
        best_format="text post or document carousel",
        notes="Dwell time is a confirmed ranking factor — a long post people "
              "actually read beats a short clever one. Outbound links in the "
              "body cost roughly 60% of reach, so put the link in the first "
              "comment. First-hour comments decide how far it travels.",
    ),
    "facebook": PlatformReach(
        platform="facebook", discovery=DISCOVERY_GRAPH,
        hook_chars=125, body_target_chars=500,
        link_policy=LINK_FIRST_COMMENT, hashtag_range=(2, 3),
        best_format="native video",
        notes="Page reach is structurally low and links are suppressed to keep "
              "people on-platform. Native video travels furthest.",
    ),
    "twitter": PlatformReach(
        platform="twitter", discovery=DISCOVERY_GRAPH,
        hook_chars=140, body_target_chars=280,
        link_policy=LINK_FIRST_COMMENT, hashtag_range=(1, 2),
        best_format="thread or native video",
        notes="A link in the post you want reach on costs reach — reply to "
              "yourself with it. Threads buy dwell time. Hashtags do not lift "
              "reach here — keep it to one, as a label, never as a strategy.",
    ),
    "snapchat": PlatformReach(
        platform="snapchat", discovery=DISCOVERY_ALGORITHMIC,
        hook_chars=80, body_target_chars=250,
        link_policy=LINK_NOT_CLICKABLE, hashtag_range=(1, 2),
        best_format="vertical video",
    ),
    "pinterest": PlatformReach(
        platform="pinterest", discovery=DISCOVERY_ALGORITHMIC,
        hook_chars=100, body_target_chars=500,
        link_policy=LINK_IN_BODY_OK, hashtag_range=(2, 3),
        best_format="tall image", search_matters=True,
        notes="Effectively a visual search engine — keyword-led descriptions "
              "keep surfacing for months.",
    ),
    "telegram": PlatformReach(
        platform="telegram", discovery=DISCOVERY_BROADCAST,
        hook_chars=120, body_target_chars=800,
        link_policy=LINK_IN_BODY_OK, hashtag_range=(2, 3),
        best_format="text with media",
        notes="Links are fine here and cost nothing, unlike every feed-based "
              "platform. Depth is rewarded: these people opted in. Hashtags "
              "are clickable and searchable inside the channel, so they work "
              "as an index of past posts rather than as a discovery lever.",
    ),
    "whatsapp": PlatformReach(
        platform="whatsapp", discovery=DISCOVERY_BROADCAST,
        hook_chars=120, body_target_chars=600,
        link_policy=LINK_IN_BODY_OK, hashtag_range=(0, 0),
        best_format="text with media",
        notes="An intrusive surface — this arrives like a message from a "
              "friend. Frequency fatigue costs you subscribers, and "
              "subscribers are the only thing reach depends on here.",
    ),
}

#: Used when a platform isn't in the table yet. Conservative on links, since
#: the penalty is common and the cost of a needless first comment is nil.
DEFAULT_REACH = PlatformReach(
    platform="unknown", discovery=DISCOVERY_GRAPH,
    hook_chars=125, body_target_chars=600,
    link_policy=LINK_FIRST_COMMENT, hashtag_range=(2, 3),
    best_format="image or video",
)


def rules_for(platform: str) -> PlatformReach:
    return REACH_RULES.get(platform.lower(), DEFAULT_REACH)


def brief_for_agent(platform: str) -> str:
    """The reach constraints for one platform, as prompt text."""

    r = rules_for(platform)
    low, high = r.hashtag_range
    if high == 0:
        hashtags = "none — this platform has no hashtag support"
    elif low:
        hashtags = f"{low}-{high}, always — every post ends with them"
    else:
        hashtags = f"0-{high}"
    link = {
        LINK_IN_BODY_OK: "a link in the body is fine here",
        LINK_FIRST_COMMENT: "NO link in the post body — it costs reach; the link "
                            "goes in the first comment",
        LINK_NOT_CLICKABLE: "links are not clickable here — do not include a URL; "
                            "refer to the profile link instead",
    }[r.link_policy]

    broadcast = r.discovery == DISCOVERY_BROADCAST
    opening = (
        f"- Only the first {r.hook_chars} characters show in the notification and "
        f"channel list, so the first line has to earn the tap."
        if broadcast else
        f"- The first {r.hook_chars} characters are all that show before the feed "
        f"truncates. The hook must land inside that or the post is never opened."
    )

    lines = [
        f"Reach mechanics for {r.platform}:",
        opening,
        f"- Body length stops helping past about {r.body_target_chars} characters.",
        f"- Links: {link}.",
        f"- Hashtags: {hashtags}.",
        f"- Best-reaching format here: {r.best_format}.",
    ]
    if r.search_matters:
        lines.append(
            "- In-platform search is a real discovery route here, so the target "
            "keyword must appear naturally in the copy. Search reach compounds; "
            "feed reach decays within days."
        )
    if broadcast:
        lines.append(
            "- No discovery surface here: reach equals subscriber count exactly. "
            "Write for people who already chose to be here — not hooks aimed at "
            "strangers who will never see this."
        )
    elif r.discovery == DISCOVERY_GRAPH:
        lines.append(
            "- Distribution starts from existing followers, so the first hour of "
            "engagement decides how far this travels. Give a real reason to "
            "reply — a genuine open question, not 'thoughts?'."
        )
    if r.notes:
        lines.append(f"- {r.notes}")
    return "\n".join(lines)


URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def split_link(caption: str, platform: str) -> tuple[str, Optional[str]]:
    """Move or strip links so a post isn't down-ranked for carrying one.

    Returns ``(body, first_comment)``. This is enforced in code rather than
    asked of the model: the penalty is mechanical and costly, and a writer
    optimizing a sentence will drop a rule it was told once. Deterministic
    here means it holds on every post, forever.
    """

    rules = rules_for(platform)
    if rules.link_policy == LINK_IN_BODY_OK:
        return caption, None

    links = URL_PATTERN.findall(caption)
    if not links:
        return caption, None

    body = URL_PATTERN.sub("", caption)
    # Tidy what removing a URL leaves behind: dangling colons and labels
    # like "Link:" or "👉" on a line of their own.
    body = re.sub(r"[ \t]*(?:👉|➡️|🔗)?[ \t]*\b(?:link|read more|more)\b[ \t]*:?[ \t]*$",
                  "", body, flags=re.IGNORECASE | re.MULTILINE)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = "\n".join(line.rstrip() for line in body.splitlines()).strip()

    if rules.link_policy == LINK_NOT_CLICKABLE:
        # Nothing to salvage — a URL here is dead text taking up the caption.
        return body, None

    return body, links[0]
