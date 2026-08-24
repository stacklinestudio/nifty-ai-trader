# V2 Build Report — Multi-Agent Intelligence

Audit date: 2026-08-24. Every claim below is backed by a command actually run in
this session or a file actually read — see evidence lines. Branch:
`feature/multi-agent-intelligence`, commit `3c283cd` (pushed to origin).

## Section 46 acceptance criteria — status

### 1. Multi-agent framework exists — DONE
`agents/` defines a `BaseAgent` ABC (`agents/base.py`) that turns any agent
exception or timeout into a structured `AgentResult` rather than raising.
14 concrete agents are implemented across `agents/research_agents.py`
(GlobalResearch, IndiaMarket, News, Technical, Volatility, Breadth,
SignalHunter) and `agents/trading_agents.py` (Options, TradeBuilder, Risk,
TradeSupervisor, Execution, PostTrade), plus `agents/trade_validator.py`
(IndependentTradeValidator). Individual files like `agents/risk_agent.py`,
`agents/breadth_agent.py` etc. are thin re-export shims to the real classes —
not stubs, just import-path convenience.

### 2. Orchestrator works — DONE
`agents/orchestrator.py::Orchestrator.run_cycle` sequences
research → consensus → signal → options → trade-build → independent
validation → risk veto → paper execution, publishing an audit event at each
stage. Verified live:
```
$ python main.py agents
{
  "paper_only": true,
  "consensus": "UNCERTAIN",
  "risk_approved": false,
  "order": null,
  "agent_count": 7
}
```
and by `pytest`: `test_orchestrator_fails_closed_without_market_data`,
`test_independent_validation_and_risk_veto_stale_candidate`,
`test_paper_execution_requires_and_receives_risk_approval` — all pass.

### 3. Event bus works — PARTIAL
`events/bus.py::EventBus` itself is correct and tested (dedup by
`event_id`, audit-sink fan-out, subscriber dispatch —
`test_event_bus_deduplicates_and_persists` passes). But **agents do not
communicate through it**. `grep -rn "\.subscribe("` across the entire repo
returns zero matches — the pub/sub dispatch path exists but is never used.
In practice the orchestrator calls `agent.run(context)` directly for every
stage and threads results forward through a plain `dict`/return values. The
bus is wired only as an audit-log sink (`EventBus(self.database.save_event)`
in `Orchestrator.__init__`), not as the inter-agent messaging layer the spec
calls for. **Action needed**: either route agent hand-offs through
`bus.publish`/`bus.subscribe`, or treat the current audit-only usage as the
accepted design and say so explicitly — this should be a deliberate choice,
not an oversight.

### 4. Risk veto works — DONE
`RiskAgent.analyze` (`agents/trading_agents.py:88-122`) is the last gate
before execution: it checks kill switch, thesis validity, risk-per-trade
ceiling, validator decision, daily limits, and data/market health, and
`ExecutionAgent` refuses to place an order unless `risk_approved` is `True`.
No code path in the new agent stack bypasses it — confirmed by reading
`orchestrator.py` end to end. Tested by
`test_independent_validation_and_risk_veto_stale_candidate` and
`test_paper_execution_requires_and_receives_risk_approval`.

### 5. Paper execution works — DONE
`ExecutionAgent.analyze` explicitly refuses to run when
`settings.trading_mode != "paper"` before it does anything else, and
otherwise places orders only through `PaperBroker`. `trading_mode` defaults
to `"paper"` (`config.py:20`). Verified by the live dry run above
(`"paper_only": true`) and `test_paper_execution_requires_and_receives_risk_approval`.

### 6. Learning memory works — PARTIAL
`learning/memory.py::MemoryStore` (append-only SQLite, no update/delete path)
is real and tested (`test_learning_memory_is_append_only`). The
hypothesis → experiment → promotion data shapes exist
(`learning/experiment_manager.py::Experiment`,
`learning/promotion_engine.py::decide`, which requires historical +
walk-forward + out-of-sample + human approval before `promote=True`) — and
critically, nothing lets a trade outcome directly mutate live parameters,
which the spec forbids. **But the pipeline is not wired to anything**:
`grep` for `learning\.|MemoryStore|Experiment|Promotion` shows these modules
are only imported by `main.py` (a `memory` CLI command that just prints
recent entries) and the test file — never by `agents/orchestrator.py` or
`PostTradeAgent`. `PostTradeAgent.analyze` (`agents/trading_agents.py:185-197`)
builds a `review` dict with `"learning_hypothesis": "None without closed
trade facts"` but never calls `record_trade`, `create_experiment`, or
`promotion_engine.decide`. **Action needed**: wire `PostTradeAgent` (or a
follow-on step) to actually write closed-trade facts into `MemoryStore` and
route promotion candidates through `promotion_engine.decide` — right now the
scaffolding is safe (no shortcut to live params) but produces no learning.

### 7. Telegram integration works with test mode — DONE, with a caveat
`integrations/telegram.py::TelegramNotifier` takes an injectable `transport`
callable, defaults to `requests.post`, retries 3x with backoff, and returns
`False` (never raises) when unconfigured or on failure — "test mode" here
means dependency-injected transport, exercised without network access by
`test_notification_formatting_and_failures_do_not_raise`, which passes.
**Caveat**: the failure handler is `except Exception: ... # noqa: BLE001`
with no logging — it swallows the real exception silently rather than the
brief's suggested pattern (narrow exception type + `logger.warning`). It is
non-fatal (criterion met) but not observable when it fails.

### 8. Discord integration works with test mode — DONE, same caveat
`integrations/discord.py::DiscordNotifier` — identical shape and same blind
`except Exception` with no logging. Same test covers it.

### 9. Obsidian export works when configured — DONE
`integrations/obsidian.py::ObsidianExporter` returns `None` when no vault
path is configured (no-op, not an error), and narrowly catches `OSError`
only (not blind) when writing. Tested by
`test_obsidian_export_is_optional_and_writes_markdown`, which checks both
the unconfigured no-op and the real markdown write.

### 10. AI provider abstraction works — DONE
`ai/provider.py` defines an `AIProvider` Protocol and an `UnavailableProvider`
default that returns confidence `0` and `risks=("AI unavailable",)` instead
of fabricating an opinion. `ai/router.py::AIRouter` wraps any provider with
caching and calls `AIAnalysis.validate()` on every result. Tested by
`test_ai_router_returns_valid_unavailable_analysis`.

### 11. All agents have structured schemas — DONE
Every agent returns `agents/contracts.py::AgentResult` (frozen dataclass:
confidence, evidence, data, sources, error, duration_ms, `.serializable()`).
Trade-specific shapes (`TradeCandidate`, `TradeThesis`, `Validation`) and the
AI-specific `AIAnalysis` (with its own `.validate()`) are separately typed.
Nothing returns a bare dict or untyped blob.

### 12. Audit trail exists — DONE
`storage/database.py` adds an `audit_events` table (`event_id` primary key,
`INSERT OR IGNORE` so history is never overwritten) plus `save_event`/`events`
methods. `Orchestrator` wires `EventBus(self.database.save_event)` so every
published event is persisted. Tested by
`test_event_bus_deduplicates_and_persists`; readable live via
`python main.py events`.

### 13. Health monitoring works — DONE
`monitoring/health.py::system_health` was extended with `ComponentHealth`
rows for `database`, `market_data`, `kite`, `ai_provider`, `telegram`,
`discord`, `obsidian`, `scheduler` — each reporting HEALTHY/DEGRADED/FAILED
independent of the hard trade-veto in `check_health`. Verified live via
`python main.py health`.

### 14. Existing tests pass — DONE
```
$ pytest -q
.............................                                            [100%]
29 passed in 9.29s
```
20 of these are the original V1 suite (matches `BUILD_REPORT.md`'s "20
passed"); none were modified or deleted.

### 15. New tests pass — DONE
The remaining 9 passing tests are all in `tests/test_v2_system.py`
(agent contract, event bus, orchestrator fail-closed, validation+risk veto,
paper execution, learning memory append-only, AI router fallback,
notification failure handling, Obsidian export) — same `pytest -q` run above.

### 16. Ruff is clean — DONE, with a caveat
```
$ ruff check .
All checks passed!
```
**Caveat**: `pyproject.toml`'s `[tool.ruff]` only sets `line-length` and
`target-version` — no `select` is configured, so ruff runs its default rule
set (pyflakes/pycodestyle basics), not the `BLE001`/`S110`
blind-except-detection rules the original interrupted session was
apparently fixing toward (per the continuation brief). The `# noqa: BLE001`
comments in `agents/base.py`, `integrations/telegram.py`, and
`integrations/discord.py` are currently inert — those rules aren't enabled,
so ruff would pass with or without them. Lint is clean, but it isn't
currently gating the blind-except pattern noted in criteria 7/8.

### 17. No secrets are committed — DONE
Checked `.env.example`/`.gitignore` diffs before committing (placeholder
values and new ignore entries only — `knowledge/*.sqlite`, `.obsidian/`) and
grepped every file touched in the V2 commit for
`api[_-]?key|secret|token|password|bearer\s*=\s*"..."`-shaped hardcoded
values: no matches.

### 18. Paper mode remains default — DONE
`config.py:20`: `trading_mode: str = os.getenv("TRADING_MODE", "paper")`.
Live-only path additionally requires `LIVE_TRADING_ENABLED=true`
(`config.py:21`), which defaults false. Confirmed via the dry run above
(`"paper_only": true`).

### 19. A complete dry run can execute the entire workflow without live orders — DONE
```
$ python main.py agents
{
  "paper_only": true,
  "consensus": "UNCERTAIN",
  "risk_approved": false,
  "order": null,
  "agent_count": 7
}
```
Ran all 7 research agents plus the trade/risk/execution chain with no market
data supplied; failed closed correctly (no order, risk not approved) rather
than fabricating a trade.

### 20. BUILD_REPORT.md is updated — DONE (this file)
This document is the V2-specific counterpart to the original
`BUILD_REPORT.md` (V1), which is left untouched since it accurately
describes the V1 delivery.

## Summary

| # | Criterion | Status |
|---|---|---|
| 1 | Multi-agent framework | DONE |
| 2 | Orchestrator | DONE |
| 3 | Event bus | **PARTIAL** — class works, not used for inter-agent comms |
| 4 | Risk veto | DONE |
| 5 | Paper execution | DONE |
| 6 | Learning memory | **PARTIAL** — scaffolded, not wired to trade outcomes |
| 7 | Telegram | DONE (blind-except, unlogged) |
| 8 | Discord | DONE (blind-except, unlogged) |
| 9 | Obsidian | DONE |
| 10 | AI provider abstraction | DONE |
| 11 | Structured schemas | DONE |
| 12 | Audit trail | DONE |
| 13 | Health monitoring | DONE |
| 14 | Existing tests pass | DONE (29 total incl. 20 V1) |
| 15 | New tests pass | DONE (9 in test_v2_system.py) |
| 16 | Ruff clean | DONE (caveat: BLE/S rules not enabled) |
| 17 | No secrets committed | DONE |
| 18 | Paper mode default | DONE |
| 19 | Dry run, no live orders | DONE |
| 20 | BUILD_REPORT.md updated | DONE |

**16/20 fully done, 2/20 done-with-caveat, 2/20 partial.** The two PARTIALs
(#3 event bus, #6 learning pipeline) are architecturally significant — they
are the two things the original spec cared most about (event-driven agents,
non-shortcut learning) — and should be finished before this is called
complete.

## Known issues carried from the continuation brief (still open)

### Position sizing likely produces "no trade" for realistic NIFTY premiums
`risk/position_sizer.py` and `risk/risk_manager.py` are unchanged by the V2
work. With `MAX_RISK_PER_TRADE=200`, `MAX_POSITION_VALUE=5000`, and NIFTY
lot size 75 (`data/instruments.py`), any option premium above roughly ₹33
already exceeds the risk-per-trade budget at the 8%-of-premium stop floor,
and a ₹100 premium alone (75 × ₹100 = ₹7,500) exceeds `MAX_POSITION_VALUE`
before risk is even checked. `tests/test_risk.py::test_position_size_obeys_risk_and_lot`
still only exercises this with an unrealistic ₹20 entry / ₹18 stop. This is
a real, unresolved design gap — not something I've changed, since the brief
is explicit that risk parameters must not be silently altered. Needs an
explicit user decision (accept frequent no-trade days at ₹200/₹5,000, or
raise the ceilings deliberately).

### Blind-except pattern in notification/agent-boundary code
See criteria 7/8/16 above — `except Exception` with no logging in
`agents/base.py`, `integrations/telegram.py`, `integrations/discord.py`.
Currently non-fatal (spec requirement met) but silent (failures aren't
observable). Not gated by ruff because the relevant rules aren't enabled in
`pyproject.toml`.
