from agenticcore.agents.base import BaseAgent


class EmailMarketerAgent(BaseAgent):
    """Builds a short lifecycle email sequence supporting the campaign goal."""

    name = "email_marketer"
    role = "lifecycle email marketer"
    system_prompt = (
        "You are a lifecycle email marketer. Given a campaign strategy, "
        "audience, and goal, draft a three-email sequence (announcement, "
        "value/education, urgency/close). For each email give a subject line, "
        "preview text, and a concise body (under 150 words) ending in one "
        "clear call to action."
    )
