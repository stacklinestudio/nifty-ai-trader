"""Prompt templates may request interpretation, never execution authority."""

PRE_MARKET = (
    "Summarize only the supplied facts. State uncertainty and do not recommend executing an order."
)
POST_TRADE = "Explain outcome using only supplied facts. Propose a research hypothesis, not a parameter change."
