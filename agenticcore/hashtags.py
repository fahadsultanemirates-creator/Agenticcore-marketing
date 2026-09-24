"""Hashtags for text posts: asked for in the prompt, guaranteed in the code.

Telling a model "2-3 hashtags" gets them most of the time, which is not the
same as every time — and a batch where one post carries tags and the next
carries none reads as an oversight on the page. So the prompt asks, and
anything that comes back short is topped up here.

The top-up is built from the brand's own keywords rather than invented from
the post, because a hashtag is a public claim about what a business does.
Keywords are curated; a tag derived from whatever the copy happened to
mention is not, and #ThreeLevelPoints on a company with no points system is
the kind of small fiction this framework exists to avoid.
"""

from __future__ import annotations

import re

from agenticcore.reach import rules_for

#: Dropped before a phrase becomes a tag. #SocialMediaMarketingForRealEstate
#: is not a hashtag anyone follows or types.
STOPWORDS = frozenset("""
a an and are as at be but by do does for from how in into is it its my of on
or our that the their they this to what when where which why with without you
your
""".split())

#: Longer than this and a tag stops being readable at a glance.
MAX_TAG_WORDS = 3


def to_tag(phrase: str) -> str:
    """Turn a keyword phrase into a single readable hashtag.

    ``"social media marketing for real estate agents"`` becomes
    ``"#SocialMediaMarketing"`` — the head of the phrase, which is the part
    people actually follow.
    """

    words = [w for w in re.split(r"[^A-Za-z0-9]+", phrase or "") if w]
    kept = [w for w in words if w.lower() not in STOPWORDS] or words
    if not kept:
        return ""
    return "#" + "".join(w[:1].upper() + w[1:] for w in kept[:MAX_TAG_WORDS])


def brand_tags(brand) -> list[str]:
    """The tags this brand can always fall back on, best first.

    Keywords lead because they are what the business wants to be found
    for; the brand name trails as a guaranteed non-empty last resort.
    """

    tags: list[str] = []
    seen: set[str] = set()
    for phrase in list(getattr(brand, "keywords", []) or []) + [getattr(brand, "name", "")]:
        tag = to_tag(phrase)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def found_in(caption: str) -> list[str]:
    return re.findall(r"#\w+", caption or "")


def ensure_hashtags(caption: str, platform: str, brand, index: int = 0) -> str:
    """Top a post up to its platform's minimum number of hashtags.

    ``index`` rotates which fallback tags are used across a batch, so three
    posts topped up on the same day do not end with the same two lines.
    Platforms with no hashtag support at all are left alone — WhatsApp
    renders them as plain text, where they are just noise.
    """

    low, high = rules_for(platform).hashtag_range
    if high == 0 or low == 0:
        return caption

    present = found_in(caption)
    if len(present) >= low:
        return caption

    taken = {t.lower() for t in present}
    pool = [t for t in brand_tags(brand) if t.lower() not in taken]
    if not pool:
        return caption
    # Rotate rather than always taking the head of the list.
    pool = pool[index % len(pool):] + pool[:index % len(pool)]

    added = pool[: max(0, low - len(present))]
    if not added:
        return caption

    body = caption.rstrip()
    # Join onto the existing tag line when there is one, so a post does not
    # end with two separate blocks of hashtags.
    if present and body.split("\n")[-1].strip().startswith("#"):
        return f"{body} {' '.join(added)}"
    return f"{body}\n\n{' '.join(added)}"
