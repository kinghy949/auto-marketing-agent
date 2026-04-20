# Architecture

## System Overview

```
              ┌─────────────────────────┐
              │   Orchestrator Agent    │  ← Human sets: KPI / budget / brand rules
              │  (goal decomposition +  │
              │       scheduling)       │
              └───────────┬─────────────┘
                          │ handoff
   ┌──────────────┬───────┼───────┬──────────────┬─────────────┐
   ▼              ▼       ▼       ▼              ▼             ▼
[Audience]   [Creative] [Media] [Experiment] [Attribution] [Guardrail]
              Agent     Buyer     Agent         Agent         Agent
   │            │        │        │             │             │
   ▼            ▼        ▼        ▼             ▼             ▼
 CDP /      DALL·E /   Meta /   Internal     MMM / MTA     Brand
 Warehouse  Midjourney Google   experiment                 lexicon
            Runway     TikTok   platform                   + ad law
                       Ads API
```

## Agent Responsibilities

| Agent | Tools | Output |
|-------|-------|--------|
| Orchestrator | Budget allocator, calendar | Weekly plan + sub-task dispatch |
| Audience | SQL on CDP, lookalike API | Audience segments (JSON) |
| Creative | LLM, DALL·E, Runway, brand-asset RAG | Copy + image/video variants ×N |
| Media Buyer | Meta / Google / TikTok Ads MCP | Buy tasks + bidding strategy |
| Experiment | Internal experiment platform API, Bayesian stats tool | Test design + early-stop rules |
| Attribution | MMM model, click/impression data | ROAS / CAC reports |
| Guardrail | Brand lexicon, ad-law rule base | Approve / reject + revision notes |

## Key Design Decisions

### Sandbox Agent usage
- Creative agent runs ffmpeg / PIL in a sandbox for long-running (5–30 min) media edits without blocking the main flow.
- Attribution agent runs MMM models (PyMC / Robyn) in a sandbox.

### Sessions
- One Session per Campaign, spanning the 4–6 week campaign lifecycle.
- Agents recall prior insights (e.g. "creative A had higher CTR last week, scale it this week").

### Handoff topology
- Not a linear pipeline — event-driven. Example: Attribution detects ROAS drop → proactively hands off to Creative for a new iteration round.

### Human-in-the-loop insertion points
- Daily budget change > 30% → mandatory human approval.
- New vertical / sensitive terms → Guardrail forces human review.
- Everything else runs autonomously.

## Real Difficulties (the parts nobody talks about)

1. **Cold-start data scarcity** — first 2 weeks in a new vertical, the Attribution agent has nothing to analyze. Need Bayesian priors.
2. **Platform API rate limits** — Meta Ads API throttles bid changes. Needs a task queue.
3. **Creative homogenization** — LLMs converge on similar copy. Needs a diversity reward signal.
4. **Attribution opacity** — post-iOS-14 MTA is unreliable. MMM as a backstop is mandatory.
5. **Audit & compliance** — EU DSA, ad-law in CN. Guardrail decisions must be fully traceable.

## Reference Prior Art

- Jasper / Copy.ai (next-gen versions are heading this way)
- Albert.ai (acquired by Zoomd) — early version of this idea
- Meta Advantage+ — platform-native, but a black box
- Bytedance "妙思" (Miaosi) — same direction, China market

## Commercial Notes

- Don't build a generic platform. Pick a vertical: DTC beauty / mobile games / B2B SaaS.
- Pricing: % of ad spend (5–10%) beats SaaS monthly fees — the value anchor is explicit.
- First customers: brands spending $50K – $500K/month. Smaller isn't worth automating; larger has internal teams.
