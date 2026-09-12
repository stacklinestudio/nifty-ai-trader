"""Phase 2 Piece 10: shared MemoryStore memory_type constants for the
automatic promotion pipeline's evidence records (Piece 9,
learning/auto_promotion_pipeline.py).

Kept in their own, dependency-free module so a reader that only needs
the constant -- not the full auto-promotion pipeline -- can depend on
it without pulling in `learning/auto_promotion_pipeline.py`'s own
heavier import chain. That chain reaches, transitively:
learning.promotion_pipeline -> backtest.daily_backtest ->
agents.orchestrator -- a real circular import found and fixed while
building Piece 10's `strategy/registry.py` (which needs this constant
to read Piece 9's real evidence, but is itself imported by
`agents/orchestrator.py`). Importing the heavier module directly from
`strategy/registry.py` would have made that a genuine import cycle;
this module breaks it structurally, not by import-order discipline.

`learning/auto_promotion_pipeline.py` itself imports these from here
too (single source of truth) -- its own module-level names are
unchanged, so every existing caller/test that does
`from learning.auto_promotion_pipeline import PROMOTION_EVALUATION_MEMORY_TYPE`
continues to work exactly as before.
"""

from __future__ import annotations

PROMOTION_EVALUATION_MEMORY_TYPE = "promotion_evaluation"
PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE = "promotion_evaluation_skipped"
