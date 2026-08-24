# V2 Architecture

V2 extends V1 rather than replacing it. Market-data and execution calculations remain deterministic. The `agents/` package produces immutable structured opinions; `events/` records the workflow in append-only SQLite audit events. `Orchestrator` detects directional conflict, runs adversarial validation, and sends every thesis to deterministic `RiskAgent` veto before `PaperBroker` execution.

AI is optional and limited to structured, fact-grounded analysis through `ai/`. It cannot emit or execute orders. `learning/` records hypotheses and requires historical, walk-forward, out-of-sample validation plus human approval before promotion.
