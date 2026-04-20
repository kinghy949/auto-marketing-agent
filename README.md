# auto-marketing-agent

Autonomous marketing campaign orchestration built on the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

Humans set KPI / budget / brand guardrails. A team of specialized agents handles audience segmentation, creative generation, media buying, experimentation, attribution, and compliance — running 24/7.

## Status

Early design phase. Architecture is documented in [`docs/architecture.md`](docs/architecture.md). No runtime code yet.

## Target Users

- DTC e-commerce brands
- Mobile game publishers
- B2B SaaS growth teams

Sweet spot: monthly ad spend $50K – $500K (too small isn't worth automating; too large already has internal teams).

## Roadmap

| Phase | Scope | Risk |
|-------|-------|------|
| P0 — Copilot | Generate creative + recommend audiences. Human runs the buys. | None |
| P1 — Semi-auto | Auto buying, all creative human-reviewed. | Low |
| P2 — Autonomous (small budget) | Closed-loop under $500/day. | Medium |
| P3 — Scale | Multi-vertical, multi-market, multi-platform. | Needs full eval pipeline. |

## License

TBD
