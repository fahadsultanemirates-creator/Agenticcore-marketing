from agenticcore.agents.base import BaseAgent


class ContentStrategistAgent(BaseAgent):
    """Turns a raw campaign brief into positioning and a content narrative."""

    name = "content_strategist"
    role = "senior marketing strategist"
    system_prompt = (
        "You are a senior marketing strategist. Given a product, target audience, "
        "and goal, produce: (1) a one-sentence positioning statement, (2) three "
        "core messaging pillars, (3) the emotional and rational hooks to lean on, "
        "and (4) a short narrative arc the rest of the campaign should follow. "
        "Be concrete and avoid generic marketing filler."
    )
