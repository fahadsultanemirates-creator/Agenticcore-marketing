from agenticcore.agents.base import BaseAgent


class SocialMediaManagerAgent(BaseAgent):
    """Drafts platform-native social posts from the shared strategy.

    Works in two modes, driven entirely by the task text. Asked for several
    platforms it writes one post each, as a section of a campaign plan a
    human reads. Asked for a single platform it returns only that post's
    copy, because the pipeline publishes that string verbatim.
    """

    name = "social_media_manager"
    role = "social media manager"
    system_prompt = (
        "You are a social media manager. Given a campaign strategy and tone, "
        "draft platform-native post copy for exactly the platform(s) the task "
        "names, respecting each platform's typical length and voice. Include "
        "relevant hashtag suggestions (max 3 per post).\n\n"
        "When the task names a SINGLE platform, your entire reply is that "
        "post, ready to paste into the scheduler as-is: no preamble, no "
        "platform label, no options to choose between, no commentary, and no "
        "bracketed asset notes. When the task names several platforms, label "
        "each post with its platform and note any visual/asset idea in "
        "brackets."
    )
