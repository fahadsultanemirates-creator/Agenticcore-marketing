"""Base class shared by every marketing agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from agenticcore.llm import LLMClient

#: Appended to every agent's own system prompt.
#:
#: These agents write copy that a single Telegram tap sends to a real page
#: under the owner's name, so an invented statistic is not a cosmetic flaw —
#: it is a false claim published to that brand's audience. Models reach for
#: concrete numbers because specificity reads as credible, which is exactly
#: what makes the failure easy to approve by mistake. The placeholder escape
#: hatch matters as much as the prohibition: told only "don't", a model
#: tends to invent anyway; given somewhere to put the unknown, it uses it.
GROUNDING_RULES = (
    "Grounding rules, which override any instruction above that would "
    "conflict with them:\n"
    "Everything you write may be published verbatim to a real audience under "
    "a real brand's name. Use only facts present in the task and context you "
    "are given. Never invent statistics, percentages, time-to-value or "
    "setup-time figures, customer/user/download counts, growth or ROI "
    "numbers, funding, awards, press mentions, certifications, pricing, "
    "named customers, quotes, or testimonials.\n"
    "When a specific number would strengthen the copy but you were not given "
    "one, either make the point qualitatively or leave a bracketed "
    "placeholder — [X%], [N customers], [timeframe] — for a human to fill "
    "in. A visible placeholder is always better than a plausible invention."
)


@dataclass
class AgentResult:
    """The output of a single agent run, plus enough metadata to trace it."""

    agent: str
    task: str
    output: str
    metadata: dict = field(default_factory=dict)


class BaseAgent:
    """A specialist worker with a fixed role, backed by a pluggable LLM client."""

    name: str = "base_agent"
    role: str = "a helpful marketing assistant"
    system_prompt: str = "You are a helpful marketing assistant."

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def full_system_prompt(self) -> str:
        """This agent's role, plus the grounding rules every agent obeys.

        Kept separate from ``system_prompt`` so a subclass only has to
        describe its own job — the rules come along automatically, including
        for specialists added later.
        """

        return f"{self.system_prompt}\n\n{GROUNDING_RULES}"

    def run(self, task: str, context: Optional[Mapping[str, Any]] = None) -> AgentResult:
        prompt = self.build_prompt(task, context or {})
        output = self.llm.complete(self.full_system_prompt(), prompt)
        return AgentResult(agent=self.name, task=task, output=output)

    def build_prompt(self, task: str, context: Mapping[str, Any]) -> str:
        lines = [f"Task: {task}"]
        if context:
            lines.append("\nContext:")
            for key, value in context.items():
                lines.append(f"- {key}: {value}")
        return "\n".join(lines)
