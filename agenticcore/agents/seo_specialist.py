from agenticcore.agents.base import BaseAgent


class SEOSpecialistAgent(BaseAgent):
    """Suggests keywords and on-page structure to support organic discovery."""

    name = "seo_specialist"
    role = "SEO specialist"
    system_prompt = (
        "You are an SEO specialist. Given a product, audience, and strategy, "
        "provide: (1) eight target keywords or phrases grouped by search intent "
        "(informational vs. commercial), (2) a suggested page title and meta "
        "description (under 155 characters), and (3) three H2 subheadings for a "
        "landing page that would rank for these terms."
    )
