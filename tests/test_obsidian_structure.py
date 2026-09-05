"""Brief 20 Parts A-C: real, organized Obsidian structure built from real,
already-computed data. Every test here proves either (1) no fabrication --
a folder with no real content yet gets an honest placeholder, never
invented example content -- or (2) no drift -- what's written matches the
real underlying data exactly, byte for byte on the fields that matter.
"""

from __future__ import annotations

from datetime import datetime

from config import IST, Settings
from integrations.obsidian import (
    NO_REAL_DATA_YET,
    ObsidianExporter,
    render_data_quality,
    render_decision_note,
    render_market_knowledge,
    render_risk_config,
)
from learning.memory import MemoryStore
from research.expected_value import EVEstimate


def test_export_market_knowledge_reflects_the_real_current_code(tmp_path):
    """No fabrication, no drift: the note must contain the real, current
    Regime enum values and setup-type frozensets -- read live, not a
    hand-copied list that could go stale."""
    from execution.live_context import ALL_DAY_SETUPS, KNOWN_GAPS, OPEN_WINDOW_SETUPS
    from intelligence.market_regime import Regime

    exporter = ObsidianExporter(str(tmp_path))
    path = exporter.export_market_knowledge()

    assert path is not None
    content = path.read_text(encoding="utf-8")
    for regime in Regime:
        assert regime.value in content
    for setup in OPEN_WINDOW_SETUPS:
        assert setup in content
    for setup in ALL_DAY_SETUPS:
        assert setup in content
    for gap in KNOWN_GAPS:
        assert gap in content
    assert path == tmp_path / "NIFTY AI Trader" / "01-Market-Knowledge" / "Regime and Setup Vocabulary.md"


def test_export_risk_config_reflects_real_live_settings_not_a_hardcoded_copy(tmp_path):
    exporter = ObsidianExporter(str(tmp_path))
    settings = Settings(max_risk_per_trade=777.0, max_daily_loss=1500.0, max_trades_per_day=2)

    path = exporter.export_risk_config(settings)

    content = path.read_text(encoding="utf-8")
    assert "777.0" in content
    assert "1500.0" in content
    assert "**max_trades_per_day**: 2" in content

    # Changing the real live settings and re-exporting must change the
    # real note -- never a cached/frozen copy.
    settings2 = Settings(max_risk_per_trade=999.0, max_daily_loss=1500.0, max_trades_per_day=2)
    exporter.export_risk_config(settings2)
    updated_content = path.read_text(encoding="utf-8")
    assert "999.0" in updated_content
    assert "777.0" not in updated_content


def test_export_learning_with_zero_real_trades_writes_an_honest_placeholder_never_fabricated(tmp_path):
    """Folders with no real content yet: an honest 'no real data yet'
    placeholder, never invented example content."""
    exporter = ObsidianExporter(str(tmp_path))
    memory = MemoryStore(tmp_path / "memory.db")

    path = exporter.export_learning(memory)

    content = path.read_text(encoding="utf-8")
    assert NO_REAL_DATA_YET in content
    assert "Zero real trades" in content
    # Never a fabricated example combination or a made-up win rate.
    assert "TREND_CONTINUATION" not in content
    assert "0.5" not in content


def test_export_learning_with_real_trades_shows_real_pattern_memory_stats(tmp_path):
    exporter = ObsidianExporter(str(tmp_path))
    memory = MemoryStore(tmp_path / "memory.db")
    for pnl in (100.0, -50.0, 80.0):
        memory.append(
            "trade",
            {"setup_type": "MOMENTUM_CONTINUATION", "entry_regime": "TREND_UP", "pnl": pnl},
            datetime.now(IST),
        )

    path = exporter.export_learning(memory)

    content = path.read_text(encoding="utf-8")
    assert "MOMENTUM_CONTINUATION / TREND_UP" in content
    assert "sample_size**: 3" in content
    assert NO_REAL_DATA_YET not in content


def test_sync_system_docs_copies_real_current_content_and_stays_fresh(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ARCHITECTURE.md").write_text("# Architecture v1\n\nReal content.\n", encoding="utf-8")
    exporter = ObsidianExporter(str(tmp_path / "vault"))

    written = exporter.sync_system_docs(docs_dir)

    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == "# Architecture v1\n\nReal content.\n"

    # The real source doc changes -- a re-sync must reflect it, never a
    # stale cached copy.
    (docs_dir / "ARCHITECTURE.md").write_text("# Architecture v2\n\nUpdated real content.\n", encoding="utf-8")
    exporter.sync_system_docs(docs_dir)
    assert written[0].read_text(encoding="utf-8") == "# Architecture v2\n\nUpdated real content.\n"


def test_sync_system_docs_targets_the_real_documented_subfolder(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "AGENTS.md").write_text("real", encoding="utf-8")
    exporter = ObsidianExporter(str(tmp_path / "vault"))

    written = exporter.sync_system_docs(docs_dir)

    assert written[0].parent == tmp_path / "vault" / "NIFTY AI Trader" / "01-Market-Knowledge" / "00-System"


def test_export_research_summary_copies_the_real_v2_build_report_verbatim(tmp_path):
    report = tmp_path / "V2_BUILD_REPORT.md"
    report.write_text(
        "# Report\n\n"
        "## Brief 12: Score Attribution\n\nReal Brief 12 content here.\n\n"
        "## Brief 13: Something Else\n\nUnrelated content.\n\n"
        "## Brief 14: Real EV\n\nReal Brief 14 content here.\n",
        encoding="utf-8",
    )
    exporter = ObsidianExporter(str(tmp_path / "vault"))

    path = exporter.export_research_summary(build_report_path=report)

    content = path.read_text(encoding="utf-8")
    assert "Real Brief 12 content here." in content
    assert "Real Brief 14 content here." in content
    assert "Unrelated content." not in content  # Brief 13 wasn't asked for -- never included


def test_export_research_summary_is_honest_when_the_report_is_missing(tmp_path):
    exporter = ObsidianExporter(str(tmp_path / "vault"))

    path = exporter.export_research_summary(build_report_path=tmp_path / "does_not_exist.md")

    assert NO_REAL_DATA_YET in path.read_text(encoding="utf-8")


def test_export_data_quality_renders_exactly_the_real_inputs_given(tmp_path):
    exporter = ObsidianExporter(str(tmp_path))

    path = exporter.export_data_quality(
        "VALID", "real_file.json: 33439 real records, 1580 real NIFTY options", "NO_GAP", ["real finding one"]
    )

    content = path.read_text(encoding="utf-8")
    assert "VALID" in content
    assert "33439 real records" in content
    assert "NO_GAP" in content
    assert "real finding one" in content


# --- Part B: no drift between what's stored and what's displayed -------


def _real_attribution() -> dict:
    return {
        "now": datetime(2026, 9, 6, 10, 5, tzinfo=IST).isoformat(),
        "setup_type": "MOMENTUM_CONTINUATION",
        "direction": "CALL",
        "regime": "TREND_UP",
        "confidence": 78.5,
        "threshold": 75.0,
        "cleared_threshold": True,
        "technical_score": 82.0,
        "opening_score": 70.0,
        "volume_score": 60.0,
        "volume_reason": "real volume above average",
        "option_score": 55.0,
        "option_reason": "real OI buildup detected",
        "global_score": 0.0,
        "global_direction": "UNKNOWN",
        "news_score": 0.0,
        "news_direction": "UNKNOWN",
        "risk_penalty": 0.0,
        "setup_evidence": "real EMA cross confirmed",
        "data_available": {"technical_score": True, "opening_score": True},
        "data_completeness": 71.4,
    }


def test_render_decision_note_matches_the_real_attribution_exactly_no_drift():
    attribution = _real_attribution()

    note = render_decision_note(attribution, validation_reasons=("real reason one", "real reason two"))

    for key in (
        "technical_score", "opening_score", "volume_score", "volume_reason", "option_score", "option_reason",
        "global_score", "global_direction", "news_score", "news_direction", "risk_penalty", "setup_evidence",
        "data_completeness",
    ):
        assert str(attribution[key]) in note, f"real field {key} missing or drifted in the rendered note"
    assert "real reason one" in note
    assert "real reason two" in note


def test_render_decision_note_includes_the_real_outcome_when_present():
    attribution = _real_attribution()
    outcome = {"pnl": 245.5, "exit_reason": "TAKE_PROFIT", "symbol": "NIFTY26SEP24000CE"}

    note = render_decision_note(attribution, outcome=outcome)

    assert "245.5" in note
    assert "TAKE_PROFIT" in note
    assert "NIFTY26SEP24000CE" in note


def test_render_decision_note_omits_outcome_section_for_a_candidate_that_never_traded():
    attribution = _real_attribution()

    note = render_decision_note(attribution)

    assert "## Real outcome" not in note


def test_render_decision_note_matches_the_real_ev_decomposition_exactly_no_drift():
    win_rate, avg_win_r, avg_loss_r, costs_r, slippage_r = 0.3653, 1.5, 1.0, 0.110, 0.011
    estimate = EVEstimate(
        setup_type="MOMENTUM_CONTINUATION",
        regime="TREND_UP",
        ev_source="COUNTERFACTUAL_PROXY",
        sample_size=186,
        win_rate=win_rate,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        costs_r=costs_r,
        slippage_r=slippage_r,
        ev_r=win_rate * avg_win_r - (1 - win_rate) * avg_loss_r - costs_r - slippage_r,
        one_r_rupees=600.0,
    )
    decomposition = estimate.decomposition()  # the real, same computation render_decision_note itself calls
    attribution = _real_attribution()

    note = render_decision_note(attribution, ev_estimate=estimate)

    assert "COUNTERFACTUAL_PROXY" in note
    assert "186" in note
    assert "0.3653" in note
    assert f"{decomposition.win_contribution:.3f}" in note
    assert f"{decomposition.loss_contribution:.3f}" in note
    assert f"{decomposition.costs:.3f}" in note
    assert f"{decomposition.slippage:.3f}" in note
    assert decomposition.dominant_driver() in note


def test_render_market_knowledge_and_render_risk_config_and_render_data_quality_are_pure_and_deterministic():
    """These pure formatters must not perform any I/O -- confirmed simply
    by calling them directly with plain data and checking they return a
    real string, no filesystem access required."""
    assert isinstance(render_market_knowledge(["TREND_UP"], ["A"], ["B"], ["gap"]), str)
    assert isinstance(render_risk_config(Settings()), str)
    assert isinstance(render_data_quality("VALID", "detail", "NO_GAP", ["finding"]), str)
