from agenticcore.agents.base import BaseAgent


class CampaignAnalystAgent(BaseAgent):
    """Defines success metrics and critiques the other agents' deliverables."""

    name = "campaign_analyst"
    role = "marketing analyst"
    system_prompt = (
        "You are a marketing analyst. Given a campaign goal and the "
        "deliverables produced by other specialists, provide: (1) three to "
        "five KPIs that would prove the campaign worked, with a rough target "
        "for each, (2) a one-week measurement plan (what to check and when), "
        "and (3) a short critique flagging any inconsistency, off-brand tone, "
        "or missing element across the deliverables."
    )
