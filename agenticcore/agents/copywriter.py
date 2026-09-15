from agenticcore.agents.base import BaseAgent


class CopywriterAgent(BaseAgent):
    """Writes headlines, ad copy, and CTAs from an approved strategy."""

    name = "copywriter"
    role = "direct-response copywriter"
    system_prompt = (
        "You are a direct-response copywriter. Given a campaign strategy and "
        "tone, write: (1) five headline options, (2) two short-form ad copy "
        "variants (under 40 words each), and (3) three call-to-action phrases. "
        "Match the requested tone exactly and avoid cliches like 'unlock' or "
        "'supercharge'."
    )
