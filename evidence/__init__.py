"""Phase 2 Piece 8: artifact-based agent evidence and end-to-end decision
trace.

This package is OBSERVABILITY/EVIDENCE infrastructure only. Nothing here
is imported by, or can influence, SignalEngine (execution/live_context.py
+ agents/research_agents.py::SignalHunterAgent), RiskAgent, TradeBuilder
Agent, ExecutionAgent, or promotion_engine -- every function in this
package either records what already happened (a real AgentResult, a real
AIAnalysis, a real decision-time snapshot) or reads it back. See each
module's own docstring for the specific existing source of truth it
references rather than duplicates -- the audit behind that decision is
recorded in the Phase 2 Piece 8 completion report, not restated here.
"""
