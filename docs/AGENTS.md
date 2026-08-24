# Agent Contracts

Each agent has a single responsibility, accepts copied context, and returns `AgentResult` with timestamp, confidence, evidence, source list, error state, and duration. Agents cannot mutate another agent's state.

Research: global, India-market, news, technical, volatility, breadth, and signal hunter. Trading: options, trade builder, independent validator, risk, execution, supervisor, post-trade, and learning. The orchestrator publishes structured events at each state transition. Risk is final authority.
