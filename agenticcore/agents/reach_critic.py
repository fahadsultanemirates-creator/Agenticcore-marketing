"""Scores a finished post on whether it will actually travel, and rewrites it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from agenticcore.agents.base import BaseAgent

#: Below this, the critic's rewrite replaces the original. Set so ordinary
#: competent copy passes untouched and only genuinely weak posts are
#: replaced — a critic that rewrites everything is just a second writer, and
#: the rewrite is not reliably better than what it replaces.
REVISE_BELOW = 7


@dataclass
class ReachVerdict:
    """The critic's read on one draft."""

    score: int
    problems: str
    revised: str
    raw: str

    @property
    def should_revise(self) -> bool:
        return self.score < REVISE_BELOW and bool(self.revised.strip())


class ReachCriticAgent(BaseAgent):
    """Judges a draft against distribution mechanics, not taste.

    Separate from the writer on purpose. Asked to write well and optimize
    for reach at once, a model reliably does the first and drifts on the
    second — the reach constraints are mechanical and get traded away for a
    nicer sentence. A second pass that only checks mechanics keeps them.
    """

    name = "reach_critic"
    role = "social distribution analyst"
    system_prompt = (
        "You are a social distribution analyst. You do not judge whether copy "
        "is pleasant. You judge one thing: will the platform show this to "
        "people, and will those people act on it.\n\n"
        "Score 1-10 against these, in order of weight:\n"
        "1. HOOK — does the first line stop a scroll, inside the truncation "
        "limit you are given? A hook that needs the reader to tap 'see more' "
        "has already failed.\n"
        "2. DWELL — is there a reason to keep reading or watching to the end? "
        "Dwell time and completion rate now outrank likes on every major "
        "platform. Specificity, an unresolved question, a list worth "
        "finishing all buy dwell; a post that gives everything away in line "
        "one buys none.\n"
        "3. SAVE OR SHARE — would a reader send this to a colleague or save it "
        "for later? Those signals outweigh likes by a wide margin. Generic "
        "encouragement is never saved.\n"
        "4. REPLY — on follower-graph platforms the first hour decides reach. "
        "Is there a genuine reason to comment? A real open question counts; "
        "'thoughts?' or 'agree?' is engagement bait and is itself "
        "down-ranked.\n"
        "5. MECHANICS — link placement, hashtag count, and length must match "
        "the rules you are given exactly.\n"
        "6. SEARCH — where in-platform search matters, does the target "
        "keyword appear naturally? Search reach compounds; feed reach decays "
        "in days.\n\n"
        "Penalize hard: opening with the brand's own name, 'excited to "
        "announce', feature lists, anything that reads as a press release, "
        "and copy that would be indistinguishable from any competitor's.\n\n"
        "Reply in exactly this format and nothing else:\n"
        "SCORE: <1-10>\n"
        "PROBLEMS:\n"
        "- <the specific mechanic that costs reach, one line each>\n"
        "REVISED:\n"
        "<the full rewritten post, ready to publish verbatim — no commentary, "
        "no options, no explanation>"
    )

    def critique(
        self,
        caption: str,
        platform: str,
        reach_brief: str,
        keyword: Optional[str] = None,
    ) -> ReachVerdict:
        context = {
            "platform": platform,
            "reach_rules": reach_brief,
            "draft_post": caption,
        }
        if keyword:
            context["target_search_keyword"] = keyword
        result = self.run(
            f"Score this {platform} draft on reach and rewrite it to travel "
            f"further. Keep its claims and its voice — change only what costs "
            f"reach.",
            context,
        )
        return parse_verdict(result.output)


def parse_verdict(text: str) -> ReachVerdict:
    """Read the critic's reply, tolerating the ways models drift from a format.

    A malformed reply yields score 10 and an empty rewrite — meaning "leave
    the original alone". Failing open matters here: a parser confused by an
    unexpected reply must never be the reason a blank caption gets queued.
    """

    score_match = re.search(r"SCORE:\s*(\d{1,2})", text, re.IGNORECASE)
    score = int(score_match.group(1)) if score_match else 10
    score = max(1, min(10, score))

    problems = ""
    problems_match = re.search(
        r"PROBLEMS:\s*(.*?)(?=REVISED:|$)", text, re.IGNORECASE | re.DOTALL
    )
    if problems_match:
        problems = problems_match.group(1).strip()

    revised = ""
    revised_match = re.search(r"REVISED:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if revised_match:
        revised = revised_match.group(1).strip()

    return ReachVerdict(score=score, problems=problems, revised=revised, raw=text)
