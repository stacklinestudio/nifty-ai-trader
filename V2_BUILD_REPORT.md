# V2 Build Report — Multi-Agent Intelligence

Audit date: 2026-08-24, updated 2026-08-26 after three follow-up fixes, and
2026-08-27 after Brief 2 (exit engine, trailing stop, regime-aware learning —
see the section near the bottom of this file). Every claim below is backed
by a command actually run in this session or a file actually read — see
evidence lines. Branch: `feature/multi-agent-intelligence`, latest commit
`1d64942` (pushed to origin).

## Update (2026-08-26): the two PARTIALs are now fixed

Criteria 3 (event bus) and 6 (learning memory) below were PARTIAL at the
original audit. Three follow-up commits addressed them plus the criteria
7/8/16 caveat, each verified with its own `pytest -q` + `ruff check .` run
before committing, per the user's request to isolate regressions:

- `974c6c5` — logging added to the three blind-except sites (agents/base.py,
  integrations/telegram.py, integrations/discord.py); telegram/discord now
  catch `OSError` specifically instead of bare `Exception`.
- `16a3a7b` — `PostTradeAgent` now takes a `MemoryStore`, actually calls
  `record_trade`/`create_experiment` when given real closed-trade facts, and
  `Orchestrator.review_trade` exposes it, publishing the previously-unused
  `TRADE_COMPLETED`/`LEARNING_CREATED` events.
- `6b8cd23` — `Orchestrator` now subscribes a handler per stage to the event
  its predecessor publishes, so stage hand-offs go through
  `EventBus.publish`/`subscribe` instead of direct calls with a hand-threaded
  context dict. **This fix introduced a real bug during development** (the
  no-candidate path redundantly re-triggered `RiskAgent` because
  `TRADE_VALIDATED` fires from two different branches) — caught by manually
  diffing the dry-run output before committing, fixed with a guard, and
  pinned with a new regression test. Left in this report as a record that
  the isolated-fix-and-verify process actually caught something, not just
  passed.

The per-criterion sections below are left as originally written (that's what
was literally true on 2026-08-24) with a note added under each of 3, 6, 7, 8,
16 pointing to the fix. The summary table at the end reflects current state.

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

**Fixed in `6b8cd23`.** `Orchestrator` now subscribes `_on_research_complete`,
`_on_signal_created`, `_on_trade_proposed`, `_on_trade_validated`, and
`_on_risk_decision` to the event each one's predecessor publishes; `grep -rn
"\.subscribe("` now finds 6 real call sites, not zero.

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

**Fixed in `16a3a7b`.** `PostTradeAgent` now records closed-trade facts and
creates a CANDIDATE `Experiment` with a fact-grounded hypothesis when given
real `outcome`/`pnl`, still deferring otherwise. `Orchestrator.review_trade`
makes this reachable. Still true, and still worth flagging: nothing in this
codebase automatically calls `review_trade` yet, because there's no
position-exit-monitoring loop that detects when a paper trade closes — that
remains separate, larger work.

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

**Fixed in `974c6c5`.** Now catches `OSError` specifically (a real
`requests.exceptions.RequestException` is a subclass of it, so genuine
network failures are still caught) and logs via `monitoring.logger` before
returning `False`.

### 8. Discord integration works with test mode — DONE, caveat fixed
`integrations/discord.py::DiscordNotifier` — identical shape and same fix
applied in `974c6c5`. Same test covers it.

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

**Partially addressed in `974c6c5`**: the blind-except pattern itself is
fixed (see 7/8 above) regardless of what ruff enforces. `pyproject.toml`'s
ruff config was deliberately left unchanged — turning on `BLE`/`S` rules now
would surface unrelated pre-existing issues across the whole V1+V2 codebase,
which is out of scope for a targeted fix. That's still a real gap: ruff
passing doesn't mean this class of bug is caught automatically going
forward, only that this specific instance was manually found and fixed.

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

## Summary (as of `6b8cd23`, 2026-08-26)

| # | Criterion | Status |
|---|---|---|
| 1 | Multi-agent framework | DONE |
| 2 | Orchestrator | DONE |
| 3 | Event bus | DONE — fixed in `6b8cd23`, agents now communicate via subscribe/publish |
| 4 | Risk veto | DONE |
| 5 | Paper execution | DONE |
| 6 | Learning memory | DONE — fixed in `16a3a7b`, PostTradeAgent wired to MemoryStore/Experiment |
| 7 | Telegram | DONE — blind-except fixed in `974c6c5` |
| 8 | Discord | DONE — blind-except fixed in `974c6c5` |
| 9 | Obsidian | DONE |
| 10 | AI provider abstraction | DONE |
| 11 | Structured schemas | DONE |
| 12 | Audit trail | DONE |
| 13 | Health monitoring | DONE |
| 14 | Existing tests pass | DONE (32 total incl. 20 original V1 + 3 new-since-audit) |
| 15 | New tests pass | DONE (12 in test_v2_system.py: 9 original + 3 added with the fixes) |
| 16 | Ruff clean | DONE (caveat below still applies) |
| 17 | No secrets committed | DONE |
| 18 | Paper mode default | DONE |
| 19 | Dry run, no live orders | DONE |
| 20 | BUILD_REPORT.md updated | DONE (this file) |

**All 20 criteria are DONE.** `ruff check .` doesn't enforce the blind-except
pattern class going forward (see criterion 16's note — left deliberately
unchanged to avoid an unrelated repo-wide lint sweep), and `review_trade`
isn't yet called by anything automatically since there's no position-exit
monitor in this codebase — both are explicitly flagged rather than hidden.

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

**Resolved in `1d64942` (2026-08-27, Brief 2).** Presented the real tradeoff
(exact numbers computed from the actual `RiskManager`/`position_sizer` code)
and the user chose to raise the caps. `MAX_RISK_PER_TRADE`/`MAX_POSITION_VALUE`
are now 600/7500, supporting one lot at ~₹100 premium.
`tests/test_risk.py` now uses a realistic ₹100 premium and the real lot size
(75) instead of the ₹20/lot-25 pairing that masked this issue. See the Brief
2 section below for the full writeup, including the flagged (not changed)
`max_daily_loss`/`max_trades_per_day` interaction this creates.

### Blind-except pattern in notification/agent-boundary code — FIXED
See criteria 7/8/16 above. `integrations/telegram.py` and
`integrations/discord.py` now catch `OSError` (not bare `Exception`) and log
via `monitoring.logger`; `agents/base.py` keeps its intentionally-broad
`except Exception` (a genuine agent-boundary contract — any agent can raise
anything) but now logs before returning. Not gated by ruff going forward,
since `BLE001`/`S110` aren't enabled in `pyproject.toml` — this was a manual
fix, not an automated one, so a future regression here wouldn't be caught
by CI alone.

---

# Brief 2 — Exit Engine, Trailing Stop, Regime-Aware Learning (2026-08-27)

Starting point: `feature/multi-agent-intelligence` at `040706e`, 32 tests
passing, ruff clean, entry pipeline event-driven and verified (all from the
prior audit above). Brief 2's confirmed gap: nothing anywhere in this
codebase ever closed a paper position. Every part below is backed by a
`pytest -q`/`ruff check .` run made *after that specific commit*, not a
final aggregate run — see each commit's message for its own evidence.

## Part A — Exit / Supervision Engine — DONE

- **A1 Position monitoring loop** — DONE. `execution/position_supervisor.py::tick`
  is the pure per-tick decision (trailing stop update, MAE/MFE tracking,
  forced-exit check, staleness check, `TradeSupervisorAgent` call).
  `Orchestrator.supervise_once` performs the real side effects on EXIT (paper
  SELL, event, `review_trade`), and `Orchestrator.run_supervised` is the real
  polling loop for live/paper use (commits `9fcb1bc`, `9a4f0af`).
- **A2 Trailing stop** — DONE. `risk/trailing_stop.py::update_stop`:
  breakeven at 1R, partial lock at 1.5R, then trail by a percent of premium
  (`Settings.trail_percent`, default 15% — documented reasoning in the module
  docstring: option-premium ATR isn't reliably available at this layer).
  Deterministic, plain Python, never agent-judged (commit `f5586ec`).
- **A3 Forced 15:15 IST square-off** — DONE. Checked first in `tick`, before
  staleness or the supervisor call, so it cannot be deferred by a
  "STRENGTHENING" read or missing data; exits at the last known valid price
  when no fresh quote exists (commit `9fcb1bc`).
- **A4 Tests** — DONE, all listed explicitly in the brief:
  - Trailing stop only ratchets favorably: `tests/test_trailing_stop.py::test_stop_never_loosens_on_adverse_price_tick`.
  - Target hit → `TAKE_PROFIT` + real SELL, broker positions go to zero:
    `tests/test_position_supervision.py::test_full_cycle_supervise_exit_review_trade_path_end_to_end`.
  - Stop hit (trailed, not original) → `STOP_LOSS` + real SELL:
    `test_tick_exits_on_trailed_stop_not_original_stop` (tick level) and
    `test_supervise_once_closes_real_position_on_stop_loss` (real broker,
    commit `f600eee`).
  - 15:15 forces exit regardless of P&L/state, via the real broker:
    `test_tick_forces_exit_at_1515_regardless_of_pnl_or_strengthening` and
    `test_supervise_once_forces_exit_at_1515_via_real_broker_regardless_of_state`.
  - Full `run_cycle → supervise → exit → review_trade` path, asserting real
    (not fabricated) P&L/MAE/MFE reach `learning.memory`:
    `test_full_cycle_supervise_exit_review_trade_path_end_to_end` — asserts
    `0 < pnl < raw_gain` (proving the recorded P&L came from the paper
    broker's actual slippage-adjusted fill, not the naive target price) and
    `mfe > 0`.
  - Stale/missing LTP does not silently continue:
    `test_tick_holds_and_notifies_on_stale_data_instead_of_guessing` and
    `test_tick_forces_exit_at_deadline_even_with_stale_data`. **Documented
    choice**: hold-and-notify (via a `SYSTEM_ERROR` audit event and a
    `logger.warning`) while before the forced-exit deadline; force-exit at
    the last known valid price once the deadline passes, regardless of
    staleness.
  ```
  $ pytest -q   # after commit f600eee (Part A complete)
  ..................................................          [100%]
  52 passed
  $ ruff check .
  All checks passed!
  $ python main.py agents
  {"paper_only": true, "consensus": "UNCERTAIN", "risk_approved": false, "order": null, "agent_count": 7}
  ```
  Not built: a CLI command to actually launch `run_supervised` against a real
  Kite quote feed. `open_position`/`supervise_once`/`run_supervised` exist
  and are tested with synthetic clocks/quote sources; wiring a live
  `KiteMarketData`-backed `quote_source` into `main.py` was not requested by
  this brief's acceptance criteria and would need a real Kite session to
  verify against, which this environment doesn't have.

## Part B — Regime-aware strategy selection — DONE

`strategy/regime_selector.py::weight_for` maps
(setup_type, market_regime, volatility_regime, breadth_participation) to a
confidence multiplier — never a hard filter, per the brief's explicit
requirement: `SignalHunterAgent` still returns the candidate at every regime
combination, just at a different weighted confidence. Every adjustment is
logged in both `AgentResult.data` (`regime_weight_multiplier`,
`regime_weight_reasons`) and the candidate's own evidence tuple.
`Orchestrator.run_cycle` now runs `signal_hunter` after its research
siblings (not alongside them) so it sees their real regime/volatility/
breadth output instead of requiring the caller to duplicate those findings.

```
$ pytest tests/test_regime_selector.py -q   # commit c7f430b
........                                                     [100%]
8 passed
```

## Part C — Learning from each trade — DONE

Closed trades recorded via `review_trade()` now carry entry regime, entry
volatility regime, entry consensus, which research agents agreed/disagreed
on direction (`agent_agreement`), final thesis confidence, whether the stop
was ever trailed, plus the outcome/pnl/mae/mfe/exit_reason from the prior
brief. `learning/pattern_memory.py` (previously a one-line `MemoryStore`
alias) now computes real win rate and expectancy per (setup_type, regime)
pair from actual recorded trades, and flags `low_confidence` whenever
`sample_size < MIN_SAMPLES_FOR_CONFIDENCE` (20) — including a perfect
3-trade win streak, per the brief's explicit "do not let 3 trades produce a
strategy promotion."

The explicit test the brief asked for —
`tests/test_learning_pipeline.py::test_a_losing_pattern_alone_cannot_bypass_promotion_engine`
— records 50 losing trades of one setup/regime pairing, computes accurate
(terrible) stats from them, files a real `CANDIDATE` experiment from that
hypothesis, and then asserts `promotion_engine.decide()` still rejects
promotion outright, since raw trade stats supply none of its four required
gates (historical/walk-forward/OOS/human approval). A companion test
(`test_settings_cannot_be_mutated_at_all_regardless_of_learning_outcome`)
confirms `Settings` is a frozen dataclass — there is no live object for any
trade outcome to write into even if `promotion_engine` were bypassed
entirely.

```
$ pytest tests/test_learning_pipeline.py -q   # commit 682d71d
......                                                        [100%]
6 passed
```

## Part D — Confidence-scaled position sizing — DONE

`risk/confidence_scaling.py::scale_quantity` scales quantity linearly
between one lot (at/below `low_confidence`, defaults to
`Settings.signal_threshold`) and the full already-approved max (at/above
`high_confidence`, 95) — it never scales past the max `position_size()`/
`RiskManager` computed under `MAX_RISK_PER_TRADE`/`MAX_POSITION_VALUE`, and
never down to zero as long as one lot was ever affordable.
`TradeBuilderAgent` calls this after `RiskManager.plan_long_option` and
recomputes `estimated_risk` from the scaled quantity — `RiskAgent`'s veto
still runs against whatever this produces, unchanged.

```
$ pytest tests/test_confidence_scaling.py -q   # commit ee5d281
.........                                                     [100%]
9 passed
```
Confirmed by a dedicated test loop across confidence 10–100 that
`estimated_risk` never exceeds the cap regardless of confidence.

## Part E — Open decisions — user decided, not silently resolved

All three presented to the user with real computed numbers (via `python -c`
against the actual code) before anything was implemented:

1. **Position sizing** (carried over from the original audit, Part D's real
   dependency). Presented: current 200/5000 caps produce `NO_TRADE` for any
   option above ~₹33 premium; supporting ~₹100 premium needs ~600/7500.
   **User decided: raise to 600/7500.** Implemented in commit `1d64942` (see
   above). `max_daily_loss` (400) is now less than one trade's own risk
   budget (600) — flagged as a code comment in `config.py`, not changed,
   since it wasn't part of what was decided and is only harmless today
   because `max_trades_per_day` stayed at 1 (see below).
2. **Multiple trades per day.** Presented: at the (now-decided) 600/trade
   risk, 2/3/4 trades/day would be 12%/18%/24% of ₹10,000 capital, and
   `max_daily_loss` only brakes after losses accumulate, not pre-emptively.
   **User decided: keep `max_trades_per_day` at 1.** No code change made —
   it was already 1.
3. **"Hero-zero" far-OTM lottery mode.** Presented: fundamentally different
   risk/reward profile the current validator/risk agent aren't designed to
   evaluate; either a separate carved-out-budget mode or don't build.
   **User decided: don't build it.** No code written for this.

## Brief 2 summary

| Part | Status |
|---|---|
| A — Exit/supervision engine | DONE |
| B — Regime-aware weighting | DONE |
| C — Learning pipeline depth | DONE |
| D — Confidence-scaled sizing | DONE |
| E — Open decisions | Presented, user decided on all three, position-sizing decision implemented |

Full suite after all of Brief 2 (commit `1d64942`):
```
$ pytest -q
........................................................................
...                                                            [100%]
75 passed
$ ruff check .
All checks passed!
```

---

# Brief 3 — Scheduler, Crash Recovery, Profit-Lock Sizing, Multi-Instrument (2026-08-27)

Starting point: `feature/multi-agent-intelligence` at `f434a8b`, 75 tests
passing, ruff clean — independently re-verified before starting, matching
the brief's own claim. Confirmed gap the brief opened with: `run_supervised`
was built and tested but never called from `main.py` or anywhere in
production code, and had no exception handling around its data-fetch calls.

## Part A — Scheduler + Crash Recovery — DONE

- **A1 Daily scheduler entrypoint** — DONE. `execution/scheduler.py::run_trading_day`
  is the injectable, testable control flow: skip if `NseCalendar.is_trading_day`
  says no, wait for market open, run one entry cycle, supervise any fill to
  its own close. `python main.py run` (commit `9062538`) wires this for
  real, with a genuine Kite-backed quote source when credentials exist
  (`build_live_quote_source`) that fails closed (returns `None`, never
  fabricates a price) otherwise. **Deployment recommendation, per the
  brief's own ask**: a fresh process per trading morning via cron/systemd,
  documented in `scheduler.py`'s module docstring — crash recovery (A3)
  already handles "a position was open when the process died," so there's
  no correctness reason to stay resident for days, and every extra day of
  uptime is another day an unrelated bug could affect a live position.
  **Known, explicitly documented gap**: there is no live entry-context
  assembly pipeline (option chain + spot + technical features + global
  market + news, combined into what `run_cycle` expects) anywhere in this
  codebase. That's separate, larger work from what this brief asked to
  close. Without it, `run` correctly produces `"no_entry"` on a real
  trading day rather than fabricating a signal — commit `9062538`'s message
  states this plainly rather than implying full live-data readiness.
- **A2 Exception handling in the polling loop** — DONE (commit `23cdb34`).
  `run_supervised` now wraps each tick in try/except, logs failures
  (`logger.error` + a `SYSTEM_ERROR` audit event), retries with the
  configured poll interval, and force-exits at the last known valid price
  (reason `FORCED_EXIT_DATA_FAILURE`) after `Settings.max_consecutive_tick_failures`
  (default 5) consecutive failures — never an unhandled crash, never an
  unbounded silent retry with an open position unmonitored.
- **A3 Crash recovery on restart** — DONE (commits `8c40276`, `0d46271`,
  `43c614f`). `execution/position_persistence.py` round-trips a full
  `PositionState` (thesis, current trailed stop, MAE/MFE, entry regime/
  consensus/agent-agreement tags) through a dict; `storage/database.py`'s
  new `open_positions` table persists it the moment a fill happens and
  clears it the moment a position actually closes; `Orchestrator.recover_open_positions`
  reconstructs every row on startup. A row that fails to reconstruct is
  **not** silently dropped or assumed fine — it's left in the table and
  escalated via a CRITICAL-severity Telegram/Discord notification plus a
  `SYSTEM_ERROR` event, exactly per the brief's explicit requirement that a
  human check the actual state by hand in that case.
- **A4 Tests** — DONE, all four scenarios listed in the brief:
  - Scheduler skips non-trading days: `test_scheduler_skips_weekend`,
    `test_scheduler_skips_configured_holiday`.
  - A simulated exception in `quote_source()` is retried, logged, and
    eventually force-exits: `test_run_supervised_retries_transient_quote_failures_without_crashing`,
    `test_run_supervised_force_exits_after_persistent_quote_failures`.
  - Restart with an open, unclosed position resumes supervision:
    `test_restart_with_open_position_resumes_supervision_not_a_fresh_cycle`,
    `test_resume_open_positions_resumes_before_any_new_entry`.
  - Restart with no open position behaves exactly as today:
    `test_restart_with_no_open_position_recovers_nothing`,
    `test_resume_open_positions_does_nothing_when_none_exist`.
  - Plus a case the brief didn't explicitly list but that the design raised:
    a corrupted/unreconstructable row is escalated, not resumed or deleted
    (`test_corrupted_open_position_row_is_escalated_not_silently_resumed`).

```
$ pytest -q   # commit 9062538 (Part A complete)
.................................................................................
[100%]
89 passed
$ ruff check .
All checks passed!
```

## Parts B, C, D — not yet started this session

Part B requires presenting real recomputed `max_trades`/`max_daily_loss`
numbers and getting explicit sign-off before touching `Settings`, per the
brief's own rule and this project's established pattern — that
presentation happens in this session's next message, not silently folded
into a commit. Part D is explicitly sequenced by the brief to come after
Parts A and B are "proven stable on NIFTY alone for real trading days,"
which cannot literally happen inside a single session (no real trading day
elapses during this conversation) — flagged for an explicit decision on how
to interpret that gate rather than silently deciding either way.

## Part B — Multi-trade-per-day, Fixed-Base Sizing, Profit Lock — DONE

1. **Fixed-base sizing** — DONE, but not via new code. Traced the actual
   call chain (`RiskManager` ← `TradeBuilderAgent` ← `Orchestrator.__init__`)
   before writing anything and found `max_risk`/`max_position_value` are
   fixed at construction from `Settings` and never take `realized_pnl` or
   any "current balance" concept as input anywhere in this codebase — the
   "must not use profit amount for trading" requirement already held by
   construction. Pinned with
   `tests/test_multi_trade_sizing.py::test_second_trade_sizing_is_unaffected_by_first_trades_profit`
   and documented explicitly in `RiskManager`'s docstring (commit `e7bcfb7`)
   so a future change that broke this would be a visible contradiction, not
   a silent regression.
2. **Daily profit target** — DONE (commit `e7bcfb7`). `DailyLimits.daily_profit_target`
   (default `None`, inert until set) blocks new entries once
   `realized_pnl >= daily_profit_target`; an already-open position is
   unaffected since `run_supervised`/`supervise_once` never consult
   `DailyLimits.can_open()` at all — proven by
   `test_already_open_position_still_supervised_normally_after_profit_target_hit`.
3. **max_trades/max_daily_loss recomputed together** — DONE (commit
   `1bc8936`), presented with real worst-case numbers before changing
   anything:

   | Option | Worst case (all lose) | Backstop trips after |
   |---|---|---|
   | 2 trades, loss cap 1200 | 1200 (12% of ₹10k) | never (no 3rd trade to gate) |
   | 3 trades, loss cap 1200 | 1800 (18%) | 2 full losses |
   | 3 trades, loss cap 1000 | 1800 (18%) | 1 full loss |

   **User decided: 3 trades, loss cap 1200.** Also found and fixed a
   hardcoded `Settings.validate()` guard that raised unless
   `max_trades_per_day == 1` exactly (the original spec's Section 15
   constraint) — replaced with a bounded range (1–4) rather than reopened
   entirely, so it stays a real guard against an unconsidered value.
4. **Same-direction re-entry safeguard** — DONE (commit `252ff71`),
   presented (allow freely / time cooldown / re-validation) before
   implementing. **User decided: require re-validation.**
   `IndependentTradeValidator.validate()` gained `blocked_reentry`:
   rejects a candidate outright when it matches the direction, setup
   type, and entry regime of a stop-out already closed today — a
   different setup type or a regime change is what proves the thesis
   isn't just re-firing broken. Does not affect
   TAKE_PROFIT/THESIS_INVALIDATED/FORCED_EXIT closes, only STOP_LOSS,
   matching the brief's literal scope.

```
$ pytest -q   # commit 1bc8936 (after the max_trades/loss-cap fix)
........................................................................
......................                                              [100%]
94 passed
$ ruff check .
All checks passed!
```

Tests required by the brief, all present: sizing for trade #2 after
trade #1's profit is unaffected (pinning test above); `can_open()` returns
`False` once the profit target is hit with trades remaining and no loss
(`test_daily_profit_target_blocks_new_entries_with_trades_remaining_and_no_loss`);
an already-open position at the moment the target is hit still gets
normally supervised (test above).

## Part C — Market-Wide Signal: News, FII/DII Flow, OI Buildup — DONE

Scoped precisely per the brief's own framing — no live feed of individual
large institutional orders exists at retail tier, and nothing here
pretends otherwise:

1. **FII/DII net flow** — DONE (commit `be25d75`). `data/fii_dii.py::to_context_value`
   normalizes NSE's daily (T+1, after-market) net flow into a
   `data.global_market.ContextValue` — one more input `GlobalResearchAgent`
   already averages, not a new agent. The normalization is the substance
   of this piece: raw FII+DII flow runs to thousands of crores while
   `GlobalResearchAgent`'s scale is tens (its own confidence formula is
   `min(80, abs(score))`) — clamped to ±20 regardless of flow size, proven
   by `test_a_single_huge_fii_day_cannot_swamp_other_disagreeing_evidence`
   (three bearish sources + one enormous FII inflow day still average out
   to BEARISH).
2. **Options OI buildup by strike** — DONE (commit `b076329`).
   `intelligence/oi_buildup.py::detect_buildup` compares OI *change* (not
   level) across two option-chain snapshots, aggregated by option type
   across all strikes — the standard, legitimate, public-data proxy for
   institutional positioning. `OptionsAgent` reports it as informational
   evidence (`oi_buildup_bias`, `oi_buildup_reasons`) without it affecting
   which option gets selected, proven by
   `test_options_agent_reports_buildup_without_it_changing_selection`.
3. **NewsAgent real wiring** — DONE (commit `28a72dc`). Confirmed the
   brief's suspicion directly: `classification` was hardcoded `"MIXED"`
   regardless of actual news content, and `NewsAgent` was never in
   `_consensus()`'s vote — "collecting headlines nobody reads" was
   literally true. Now calls the already-existing, already-unused
   `data.news.aggregate_sentiment` for a real direction, confidence capped
   at 40. Deliberately did **not** add it to `_consensus()`'s literal 3-way
   vote (see commit message: folding a 4th voter into a `>= 2` threshold
   would weaken, not strengthen, "must not trade on a single headline").
   Instead `SignalHunterAgent` applies a separate ±5%-capped confidence
   nudge when news aligns/contradicts the candidate's direction, proven
   unable to flip a weak candidate strong by
   `test_signal_hunter_news_nudge_is_bounded_and_cannot_flip_low_confidence_to_high`.
4. **No single signal overrides validator/risk veto** — DONE by
   construction across all three: FII/DII is bounded before averaging,
   OI buildup is informational only, news is a ±5%-capped nudge. None of
   the three can gate a trade decision on their own.

```
$ pytest -q   # commit 28a72dc (Part C complete)
.........................................................................
.........................................                              [100%]
113 passed
$ ruff check .
All checks passed!
```

## Part D — Multi-Instrument Expansion — DEFERRED (user decision)

Presented the sequencing conflict directly: the brief requires Parts A/B
"proven stable on NIFTY alone for real trading days" before Part D, which
cannot literally happen inside one session. **User decided: defer Part D
entirely.** No code written for it this session — the 7 files that
hardcode "NIFTY" (`main.py`, `agents/research_agents.py`, `config.py`,
`integrations/obsidian.py`, `integrations/discord.py`, `dashboard.py`,
`data/instruments.py`) are unchanged.

## Brief 3 summary

| Part | Status |
|---|---|
| A — Scheduler + crash recovery | DONE |
| B — Multi-trade sizing + profit lock | DONE |
| C — Market-wide signal (FII/DII, OI, news) | DONE |
| D — Multi-instrument | Deferred (user decision) |

Final state this session: commit `28a72dc`, 113 tests passing, ruff clean.
