from agenticcore.agents import CopywriterAgent, SocialMediaManagerAgent
from agenticcore.agents.base import GROUNDING_RULES, BaseAgent


class RecordingLLM:
    """Captures exactly what system prompt the agent sent."""

    def __init__(self):
        self.system = None

    def complete(self, system, prompt):
        self.system = system
        return "ok"


def test_every_agent_sends_the_grounding_rules():
    for agent_class in (CopywriterAgent, SocialMediaManagerAgent, BaseAgent):
        llm = RecordingLLM()
        agent_class(llm).run("Write something")
        assert GROUNDING_RULES in llm.system, agent_class.__name__


def test_the_agents_own_role_still_leads_the_prompt():
    """Order matters: EchoLLMClient and any log reader take line one as the role."""

    llm = RecordingLLM()
    agent = CopywriterAgent(llm)
    agent.run("Write something")

    assert llm.system.startswith(agent.system_prompt)


def test_a_new_specialist_inherits_the_rules_without_opting_in():
    class BrandNewSpecialist(BaseAgent):
        name = "brand_new"
        system_prompt = "You are a podcast producer."

    llm = RecordingLLM()
    BrandNewSpecialist(llm).run("Do the thing")

    assert "You are a podcast producer." in llm.system
    assert GROUNDING_RULES in llm.system
