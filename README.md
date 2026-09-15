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

### Grounding rules

Every agent's system prompt carries a shared set of grounding rules
(`GROUNDING_RULES` in `agenticcore/agents/base.py`) forbidding invented
statistics, customer counts, pricing, timeframes, named customers, awards
and testimonials. Where a number would help but wasn't supplied in the
brief, agents leave a bracketed placeholder — `[X%]`, `[20-minute]` — for a
human to fill in during approval.

This exists because a single Telegram tap publishes copy verbatim under a
real brand's name, and models reach for concrete figures precisely because
specificity reads as credible — which is what makes a fabricated number easy
to approve by mistake. Subclass `BaseAgent` and the rules come along
automatically; a new specialist doesn't have to opt in.

Every agent is a thin wrapper (`agenticcore/agents/base.py`) around a
pluggable `LLMClient`. The default backend calls the Claude API via the
`anthropic` SDK; an `EchoLLMClient` offline backend is included so the
pipeline, tests, and examples all run without an API key or network access.

## The full pipeline: brands -> agents -> approval -> publishing

On top of the agent pipeline above sits a second layer for running this
across several of your own projects and actually getting posts onto pages,
with a human approval gate in between:

```
brands/<slug>.json (audience, tone, goal, linked pages)
      |
      v
MarketingOrchestrator          --> one strategy for the brand, then one
      |                            post per page, written for that platform
      v
Creative tool (per brand's       --> "image": Ideogram / Grok
  "media" setting)                   "video": HeyGen avatar video (spoken script
      |                                        drafted separately from the caption)
      v                                "none": text-only posts
DraftStore (SQLite)            --> one PostDraft per configured page, status=pending_approval
      |
      v
TelegramApprovalBot            --> sends caption + media with Approve/Reject buttons
      |
      v  (you tap a button)
ContentPipeline.process_decisions()
      |
      +--> Approve --> AyrshareClient.publish() --> that one page, live
      +--> Reject  --> marked rejected, nothing goes out
```

Nothing publishes without a Telegram tap, and only taps from a chat id you
configured count — the allow-list fails closed, so a bot with no chat id set
refuses every tap rather than trusting whoever finds it first.

Ayrshare is the publishing layer because it already has App Review approval
with Meta/LinkedIn/X/etc. — you link each page once in Ayrshare's dashboard
(a plain OAuth click) instead of running your own Meta developer app through
review for every project.

### Project layout

```
agenticcore/
  llm.py                  # LLMClient protocol + Anthropic/offline backends
  config.py                # load_env(): reads .env into os.environ
  orchestrator.py          # CampaignBrief, CampaignPlan, MarketingOrchestrator
  brands.py                # BrandProfile / BrandRegistry (one JSON file per project)
                           #   incl. the per-brand "media" toggle
  queue.py                 # PostDraft / DraftStore (SQLite-backed approval queue)
  pipeline.py              # ContentPipeline: wires everything below together
  agents/
    base.py                # BaseAgent, AgentResult, GROUNDING_RULES
    content_strategist.py
    copywriter.py
    seo_specialist.py
    social_media_manager.py
    email_marketer.py
    campaign_analyst.py
  creative/                # image/video generation backends
    ideogram.py             # Ideogram v3 image generation
    grok.py                 # xAI Grok image generation
    heygen.py                # HeyGen avatar video generation
    base.py                  # ImageGenerator/VideoGenerator protocols + offline stubs
  publishing/
    ayrshare.py              # posts to your linked pages via Ayrshare
  approval/
    telegram_bot.py          # sends drafts to Telegram, polls Approve/Reject taps
brands/
  example.json              # copy this per project you want to run
examples/
  run_campaign.py            # agent pipeline only, prints a Markdown plan
  run_pipeline.py             # full flow: generate -> Telegram approval -> publish
.github/workflows/
  tests.yml                  # runs pytest on 3.10 and 3.13, no credentials
tests/
  test_orchestrator.py       # agent wiring, offline backend
  test_queue.py               # DraftStore CRUD + column allow-list
  test_pipeline.py             # queueing + approve/reject, fake bot & publisher
  test_telegram_bot.py          # approval authorization (including fail-closed)
  test_config.py                 # .env parsing and precedence
  test_agents.py                  # grounding rules reach every agent
```

### Setting it up for real

1. **Ayrshare** (publishing): create an account, link each Facebook/Instagram/
   LinkedIn/etc. page you already own from the Ayrshare dashboard (one-time
   OAuth click per page, no Meta developer app needed), and copy your API
   key. If you're running several projects with separate page sets, use
   Ayrshare's multi-profile plan and copy each project's Profile-Key into
   that project's `ayrshare_profile_key` field.
2. **Telegram** (approval): message `@BotFather`, run `/newbot`, copy the
   token. Add the bot to whatever chat you want approvals in and grab the
   chat id (send it any message, then check
   `https://api.telegram.org/bot<token>/getUpdates`).
3. **Creative tools** (optional, for images/video instead of text-only
   posts): set `IDEOGRAM_API_KEY` and/or `XAI_API_KEY` (Grok) for images, or
   `HEYGEN_API_KEY` + `HEYGEN_AVATAR_ID` + `HEYGEN_VOICE_ID` for avatar video.
   All three HeyGen variables are required together — the avatar and voice
   ids come from your HeyGen dashboard. Then set `"media"` on each brand
   (see below) to say which it should use.
4. Copy `brands/example.json` to `brands/<your-project-slug>.json` per
   project (5-6 of them, to start) and fill in its audience, tone, goal, and
   which channels/pages it owns.
5. Fill in `.env` from `.env.example` with all of the above.

```bash
python examples/run_pipeline.py <your-project-slug>            # queue that brand, then listen
python examples/run_pipeline.py --all                          # queue every brand, then listen
python examples/run_pipeline.py --listen                       # listen only, generate nothing
python examples/run_pipeline.py <your-project-slug> --dry-run  # preview only, no network calls
```

`--listen` is what you want after a restart. Drafts live in SQLite, so a run
that was interrupted leaves them `pending_approval`; `--listen` picks those
up and waits for taps instead of generating a fresh campaign nobody asked
for. Every brand's approval chat is authorized at startup in this mode, not
just the one being generated for.

### Choosing creative per brand

A brand's `"media"` field decides what its posts carry. One asset is
generated per campaign and shared across that brand's pages, the way a
single graphic gets reused across a Facebook page and an Instagram account —
the captions are still written per page.

| `"media"` | Needs | Produces |
| --- | --- | --- |
| `"image"` (default) | `IDEOGRAM_API_KEY` or `XAI_API_KEY` | One graphic |
| `"video"` | `HEYGEN_API_KEY` + `HEYGEN_AVATAR_ID` + `HEYGEN_VOICE_ID` | One avatar video, from a spoken script drafted separately from the captions |
| `"none"` | nothing | Text-only posts |

A brand asking for creative you haven't configured a backend for degrades to
text-only and says so on startup, rather than failing the campaign.

## Quickstart

```bash
pip install -e ".[dev]"   # or: pip install -r requirements.txt
cp .env.example .env      # then fill in ANTHROPIC_API_KEY

python examples/run_campaign.py            # real API call if key is set
python examples/run_campaign.py --dry-run  # always offline
```

Both example scripts read `.env` on startup (`agenticcore.config.load_env`),
so filling that file in is enough — no `export` needed. Real environment
variables still win over the file. The library itself reads only
`os.environ`, so if you import `agenticcore` from your own code, call
`load_env()` yourself or export the keys.

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
