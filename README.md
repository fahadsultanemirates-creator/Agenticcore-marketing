# AgenticCore Marketing Framework

A small, dependency-light multi-agent framework for running AI-driven
marketing campaigns with Claude. You give it a campaign brief; it fans the
work out to specialist agents (strategy, copy, social, email, SEO) and rolls
their output up into a single campaign plan with KPIs and a critique pass.

## How it works

```
CampaignBrief
      |
      v
ContentStrategistAgent  --> positioning, pillars, narrative
      |
      v   (shared strategy context)
      +--> CopywriterAgent          --> headlines, ad copy, CTAs
      +--> SocialMediaManagerAgent  --> LinkedIn / X / Instagram posts
      +--> EmailMarketerAgent       --> 3-email lifecycle sequence
      +--> SEOSpecialistAgent       --> keywords, meta, headings
      |
      v
CampaignAnalystAgent  --> KPIs, measurement plan, cross-deliverable critique
      |
      v
CampaignPlan (Markdown-renderable)
```

Every agent is a thin wrapper (`agenticcore/agents/base.py`) around a
pluggable `LLMClient`. The default backend calls the Claude API via the
`anthropic` SDK; an `EchoLLMClient` offline backend is included so the
pipeline, tests, and examples all run without an API key or network access.

## Project layout

```
agenticcore/
  llm.py                  # LLMClient protocol + Anthropic/offline backends
  orchestrator.py          # CampaignBrief, CampaignPlan, MarketingOrchestrator
  agents/
    base.py                # BaseAgent, AgentResult
    content_strategist.py
    copywriter.py
    seo_specialist.py
    social_media_manager.py
    email_marketer.py
    campaign_analyst.py
examples/
  run_campaign.py           # end-to-end demo
tests/
  test_orchestrator.py      # wiring tests using the offline backend
```

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in ANTHROPIC_API_KEY

python examples/run_campaign.py            # real API call if key is set
python examples/run_campaign.py --dry-run  # always offline
```

## Usage

```python
from agenticcore.orchestrator import CampaignBrief, MarketingOrchestrator

orchestrator = MarketingOrchestrator()  # uses ANTHROPIC_API_KEY if set
brief = CampaignBrief(
    product="AgenticCore, a multi-agent marketing framework",
    audience="Growth marketers at early-stage B2B SaaS companies",
    goal="Drive qualified demo signups",
    tone="confident, technical, no hype",
    channels=["social", "email", "seo"],  # drop channels you don't need
)

plan = orchestrator.run_campaign(brief)
print(plan.to_markdown())
```

Each agent can also be used standalone:

```python
from agenticcore.agents import CopywriterAgent
from agenticcore.llm import AnthropicLLMClient

copywriter = CopywriterAgent(AnthropicLLMClient())
result = copywriter.run("Write three headline options", {"tone": "playful"})
print(result.output)
```

## Extending the framework

- **Add a new specialist**: subclass `BaseAgent`, set `name` and
  `system_prompt`, and wire it into `MarketingOrchestrator.run_campaign`.
- **Add a new channel toggle**: gate a specialist's call behind
  `if "<channel>" in brief.channels` the same way social/email/SEO are.
- **Swap the model provider**: implement `LLMClient.complete` for your
  provider and pass an instance to `MarketingOrchestrator(llm=...)`.

## Testing

```bash
pytest
```

Tests run entirely against `EchoLLMClient`, so they require no API key and
make no network calls.
