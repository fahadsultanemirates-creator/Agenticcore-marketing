from agenticcore.llm import EchoLLMClient
from agenticcore.orchestrator import CampaignBrief, MarketingOrchestrator


def make_brief(**overrides) -> CampaignBrief:
    defaults = dict(
        product="Widget Pro",
        audience="Backend developers",
        goal="Increase trial signups",
    )
    defaults.update(overrides)
    return CampaignBrief(**defaults)


def test_run_campaign_produces_every_default_section():
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient())
    plan = orchestrator.run_campaign(make_brief())

    assert set(plan.results) == {"strategy", "copy", "social", "email", "seo", "analysis"}
    for result in plan.results.values():
        assert result.output


def test_channels_filter_which_specialists_run():
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient())
    plan = orchestrator.run_campaign(make_brief(channels=["email"]))

    assert "email" in plan.results
    assert "social" not in plan.results
    assert "seo" not in plan.results
    # Strategy, copy, and analysis always run regardless of channel selection.
    assert "strategy" in plan.results
    assert "copy" in plan.results
    assert "analysis" in plan.results


def test_strategy_output_is_passed_into_channel_context():
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient())
    plan = orchestrator.run_campaign(make_brief(channels=["social"]))

    strategy_output = plan.results["strategy"].output
    assert strategy_output in plan.results["social"].output


def test_to_markdown_includes_brief_and_all_sections():
    orchestrator = MarketingOrchestrator(llm=EchoLLMClient())
    plan = orchestrator.run_campaign(make_brief())

    markdown = plan.to_markdown()
    assert "Widget Pro" in markdown
    assert "## Strategy" in markdown
    assert "## KPIs & critique" in markdown
