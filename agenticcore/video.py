"""Video packages: one script, plus the two things you paste when posting it.

The same vertical video goes to TikTok, Instagram Reels, Facebook Reels and
YouTube Shorts. The script is shared; only the posting metadata differs. So
a package is three pieces, delivered separately because that is how they get
used:

1. the **script** — the words spoken, timed to a target length
2. the **thumbnail text** — 4-6 words, burned onto the cover frame
3. the **brief** — one caption written to fit every destination

The brief is written once rather than per platform, and the binding limit is
YouTube's: a Shorts **title** is 100 CHARACTERS, not 100 words — roughly
fifteen words. TikTok now allows 4,000 characters and Instagram 2,200, so
anything that fits YouTube fits everywhere. The brief's first line is
therefore written as a standalone hook under 100 characters: paste it as the
YouTube title, and it is also the opening line of the caption everywhere
else.

Ten seconds and sixty seconds are different shapes, not the same script
trimmed. Ten seconds is one idea: hook, turn, payoff. Sixty has room for a
setup, three beats and a close.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from agenticcore.research.parsing import parse_fields

#: YouTube Shorts title limit — the tightest of any destination, and the
#: reason one brief can serve all four.
YOUTUBE_TITLE_CHARS = 100

#: Roughly how many spoken words fit per second at a natural social pace.
WORDS_PER_SECOND = 2.5

THUMBNAIL_MIN_WORDS, THUMBNAIL_MAX_WORDS = 3, 6


@dataclass
class VideoPackage:
    """A script plus the two pieces you paste when uploading it."""

    brand_slug: str
    seconds: int
    script: str
    thumbnail_text: str = ""
    brief: str = ""
    hashtags: list[str] = field(default_factory=list)
    topic: str = ""

    @property
    def title_line(self) -> str:
        """The brief's first line, which doubles as the YouTube title."""

        return self.brief.strip().splitlines()[0] if self.brief.strip() else ""

    @property
    def spoken_words(self) -> int:
        return len(self.script.split())

    @property
    def estimated_seconds(self) -> float:
        return self.spoken_words / WORDS_PER_SECOND

    def warnings(self) -> list[str]:
        """Everything about this package that would cause a problem on upload.

        Checked in code because these are hard limits: a title over 100
        characters is rejected by YouTube, and a script that runs 20 seconds
        long in a 10-second slot has to be recut.
        """

        problems = []
        title = self.title_line
        if len(title) > YOUTUBE_TITLE_CHARS:
            problems.append(
                f"Title line is {len(title)} characters — YouTube Shorts cuts at "
                f"{YOUTUBE_TITLE_CHARS}."
            )
        words = len(self.thumbnail_text.split())
        if self.thumbnail_text and not (THUMBNAIL_MIN_WORDS <= words <= THUMBNAIL_MAX_WORDS):
            problems.append(
                f"Thumbnail text is {words} words — aim for "
                f"{THUMBNAIL_MIN_WORDS}-{THUMBNAIL_MAX_WORDS} so it reads at a glance."
            )
        # Asymmetric on purpose. Overrunning means recutting the video, so
        # it is flagged at 20%. Running short only means holding a beat or
        # letting a visual breathe, which is normal — only a script at half
        # the target is a real problem, and that means the model under-wrote.
        estimated = self.estimated_seconds
        if estimated > self.seconds * 1.2:
            problems.append(
                f"Script runs about {estimated:.0f}s in a {self.seconds}s slot "
                f"({self.spoken_words} words) — it will need recutting."
            )
        elif self.script.strip() and estimated < self.seconds * 0.5:
            problems.append(
                f"Script is only about {estimated:.0f}s for a {self.seconds}s slot "
                f"({self.spoken_words} words) — probably under-written."
            )
        if not self.script.strip():
            problems.append("No script came back.")
        return problems

    def as_telegram_messages(self) -> list[str]:
        """The package split the way it gets used: one message per paste.

        Three messages rather than one, because each is copied into a
        different field at upload time and a single blob means picking it
        apart by hand every time.
        """

        header = f"{self.seconds}s video"
        if self.topic:
            header += f" — {self.topic}"

        messages = [f"[{header}]\nSCRIPT (read aloud):\n\n{self.script.strip()}"]
        if self.thumbnail_text:
            messages.append(f"[THUMBNAIL TEXT — {self.thumbnail_text.split().__len__()} words]\n\n"
                            f"{self.thumbnail_text.strip()}")
        if self.brief:
            body = self.brief.strip()
            if self.hashtags:
                body += "\n\n" + " ".join(self.hashtags)
            messages.append(
                f"[CAPTION — first line is the YouTube title "
                f"({len(self.title_line)}/{YOUTUBE_TITLE_CHARS} chars)]\n\n{body}"
            )
        return messages


PACKAGE_LABELS = ("SCRIPT", "THUMBNAIL", "TITLE", "BRIEF", "HASHTAGS")


def parse_package(text: str, brand_slug: str, seconds: int, topic: str = "") -> VideoPackage:
    """Read a package out of the video agent's reply."""

    fields = parse_fields(text, PACKAGE_LABELS)
    title = fields.get("title", "").strip()
    brief = fields.get("brief", "").strip()
    # The title is the caption's first line; if the model gave them
    # separately, join them so there is one thing to paste.
    if title and not brief.startswith(title):
        brief = f"{title}\n\n{brief}".strip()

    raw_tags = fields.get("hashtags", "")
    hashtags = [t if t.startswith("#") else f"#{t}"
                for t in re.split(r"[\s,]+", raw_tags) if t.strip(" ,#")]

    return VideoPackage(
        brand_slug=brand_slug,
        seconds=seconds,
        script=fields.get("script", ""),
        thumbnail_text=fields.get("thumbnail", ""),
        brief=brief,
        hashtags=hashtags[:5],
        topic=topic,
    )


def _shape_for(seconds: int) -> str:
    """The structural brief for a given length.

    Ten seconds and sixty are different forms, not the same script trimmed —
    a 60-second script cut to 10 loses its payoff, and a 10-second idea
    stretched to 60 is padding.
    """

    if seconds <= 15:
        return (
            "ONE idea only. Hook in the first spoken sentence, a turn, a payoff. "
            "No setup, no preamble, no sign-off. Every word earns its place."
        )
    if seconds <= 30:
        return (
            "Hook, one concrete example, payoff. Room for a single specific "
            "detail — a number from the brief, a named situation — and nothing more."
        )
    return (
        "Hook, brief setup, three beats that build, then a close that lands. "
        "The hook still has to work in the first two seconds; the extra length "
        "buys depth, not a slower start."
    )


VIDEO_SYSTEM = (
    "You write short vertical video for TikTok, Instagram Reels, Facebook "
    "Reels and YouTube Shorts, plus the two things that get pasted at upload "
    "time.\n\n"
    "How this is actually used: the same video goes to all four "
    "destinations. You write ONE script, ONE thumbnail line and ONE caption "
    "that works everywhere. Do not write per-platform variants.\n\n"
    "SCRIPT — only the words spoken aloud. No scene directions, no camera "
    "notes, no speaker labels, no emoji, no hashtags, no markdown. It will "
    "be read verbatim, so anything that is not speech ends up being said.\n\n"
    "THUMBNAIL — 3 to 6 words, burned onto the cover frame. It has to make "
    "sense with no other context and read at a glance on a phone. Not a "
    "sentence, not a summary — the sharpest phrase in the video.\n\n"
    "TITLE — under 100 CHARACTERS, hard limit. This is the YouTube Shorts "
    "title and also the first line of the caption everywhere else, so it "
    "must stand alone. Count the characters.\n\n"
    "BRIEF — two or three sentences of caption under the title. Written once "
    "to fit the tightest destination, so keep it short.\n\n"
    "HASHTAGS — 3 to 5, lowercase, no hash symbol needed.\n\n"
    "The first two seconds decide whether anyone sees the rest, so the first "
    "spoken line is the whole game. Open on the viewer's problem, never on "
    "the brand's name.\n\n"
    "Reply in exactly this format and nothing else:\n\n"
    "SCRIPT: <the spoken words>\n"
    "THUMBNAIL: <3-6 words>\n"
    "TITLE: <under 100 characters>\n"
    "BRIEF: <two or three sentences>\n"
    "HASHTAGS: <3-5, comma separated>"
)


class VideoPackageAgent:
    """Writes one script plus its thumbnail and caption, to a target length."""

    name = "video_packager"

    def __init__(self, llm):
        self.llm = llm

    def write(
        self,
        brand,
        seconds: int,
        topic: str = "",
        website_brief: str = "",
        avoid: Optional[list[str]] = None,
    ) -> VideoPackage:
        target_words = int(seconds * WORDS_PER_SECOND)
        lines = [
            f"Brand: {brand.name}",
            f"Audience: {brand.audience}",
            f"Tone: {brand.tone}",
            "",
            f"Target length: {seconds} seconds — about {target_words} spoken words.",
            f"Shape: {_shape_for(seconds)}",
        ]
        if topic:
            lines += ["", f"This video is about: {topic}"]
        if website_brief:
            lines += ["", website_brief]
        if brand.forbidden_claims:
            lines += ["", "Never claim: " + "; ".join(brand.forbidden_claims)]
        if avoid:
            lines += ["", "Already covered — take a different angle than these:",
                      *(f"- {a}" for a in avoid[:10])]

        output = self.llm.complete(VIDEO_SYSTEM, "\n".join(lines))
        return parse_package(output, brand.slug, seconds, topic=topic)
