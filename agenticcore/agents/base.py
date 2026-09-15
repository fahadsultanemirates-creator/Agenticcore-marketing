"""Base class shared by every marketing agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from agenticcore.llm import LLMClient


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

    def run(self, task: str, context: Optional[Mapping[str, Any]] = None) -> AgentResult:
        prompt = self.build_prompt(task, context or {})
        output = self.llm.complete(self.system_prompt, prompt)
        return AgentResult(agent=self.name, task=task, output=output)

    def build_prompt(self, task: str, context: Mapping[str, Any]) -> str:
        lines = [f"Task: {task}"]
        if context:
            lines.append("\nContext:")
            for key, value in context.items():
                lines.append(f"- {key}: {value}")
        return "\n".join(lines)
