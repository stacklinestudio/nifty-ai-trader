"""Brief 20: Obsidian as a structured, write-only knowledge layer.

**The one non-negotiable boundary, unchanged from before this brief and
made explicit here**: Obsidian remains write-only. No agent, orchestrator
constructor, or run_cycle path ever reads from a vault path for anything
-- see tests/test_obsidian_write_only.py for the structural proof (a real
grep-based test over every source file, not a convention taken on faith).
If a future brief ever proposes an agent reading from this knowledge
base, that is a new, separate architectural decision requiring its own
explicit safety review (it must never bypass learning/promotion_engine.py's
validated-experiment gate the way a raw "lessons learned" note read
directly into a live decision would).

Every note this module writes is built from real, already-computed data
passed in by the caller -- this module never fabricates a fact, invents
narrative, or computes new analysis; it formats and organizes what
already exists elsewhere in this project (score_attribution,
CounterfactualRecord, EVEstimate/EVDecomposition, PatternStats, real
Settings values, real docs/ files).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config import Settings
    from learning.memory import MemoryStore
    from research.expected_value import EVEstimate

NO_REAL_DATA_YET = "No real data yet."


class ObsidianExporter:
    def __init__(self, vault_path: str = "") -> None:
        self.root = Path(vault_path) / "NIFTY AI Trader" if vault_path else None

    def export(self, category: str, title: str, facts: dict[str, Any]) -> Path | None:
        """The original, generic primitive (pre-Brief-20): a flat
        `- **key**: value` bullet list. Left unmodified -- existing
        callers (Trade Journal, Daily Research) are updated to pass a
        real, structured `category` path (e.g. "06-Trades/2026/2026-09-06")
        rather than this method itself changing shape."""
        if self.root is None:
            return None
        try:
            destination = self.root / category
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / f"{title}.md"
            body = "\n".join(f"- **{key}**: {value}" for key, value in facts.items())
            path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
            return path
        except OSError:
            return None

    def export_markdown(self, category: str, title: str, body: str) -> Path | None:
        """Brief 20: writes pre-rendered, structured markdown (headers,
        sections) rather than `export()`'s flat bullet list -- for the
        richer notes Part B asks for ("readable... better presented",
        not just a flat dump of facts)."""
        if self.root is None:
            return None
        try:
            destination = self.root / category
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / f"{title}.md"
            path.write_text(body, encoding="utf-8")
            return path
        except OSError:
            return None

    # --- Part A: real structural sections ------------------------------

    def export_market_knowledge(self) -> Path | None:
        """01-Market-Knowledge: real regime/setup vocabulary, read live
        from the actual running code -- never a hand-copied list that
        could silently drift from what the code really does."""
        from execution.live_context import ALL_DAY_SETUPS, KNOWN_GAPS, OPEN_WINDOW_SETUPS
        from intelligence.market_regime import Regime

        return self.export_markdown("01-Market-Knowledge", "Regime and Setup Vocabulary", render_market_knowledge(
            regimes=[r.value for r in Regime],
            open_window_setups=sorted(OPEN_WINDOW_SETUPS),
            all_day_setups=sorted(ALL_DAY_SETUPS),
            known_gaps=list(KNOWN_GAPS),
        ))

    def export_risk_config(self, settings: Settings) -> Path | None:
        """03-Risk: real, current risk config read live from `settings`
        at export time -- never a hardcoded copy that could drift from a
        real config change."""
        return self.export_markdown(
            "03-Risk",
            "Current Risk Configuration",
            render_risk_config(settings),
        )

    def export_data_quality(
        self,
        archive_status: str,
        archive_detail: str,
        gap_check_status: str,
        field_discovery_summary: list[str],
    ) -> Path | None:
        """04-Data: real data-quality status -- the caller supplies real,
        already-computed inputs (Brief 18's validate_archive result,
        Brief 16's gap-check result, Brief 19's real field-discovery
        conclusions) rather than this method recomputing or guessing at
        any of them."""
        return self.export_markdown(
            "04-Data",
            "Data Quality Status",
            render_data_quality(archive_status, archive_detail, gap_check_status, field_discovery_summary),
        )

    def export_research_summary(self, build_report_path: Path = Path("V2_BUILD_REPORT.md")) -> Path | None:
        """05-Research: copies the real, already-written Brief 12/14/15
        sections of this project's own build report verbatim -- zero
        fabrication risk, since it is literally the existing real text,
        not a re-authored summary that could drift from it."""
        if not build_report_path.exists():
            return self.export_markdown(
                "05-Research",
                "Score Attribution and EV Findings",
                f"# Score Attribution and EV Findings\n\n{NO_REAL_DATA_YET} "
                f"({build_report_path} not found.)\n",
            )
        sections = _extract_report_sections(
            build_report_path,
            (
                "## Brief 12:",
                "## Brief 14:",
                "## Brief 15 (Phase 2c):",
            ),
        )
        body = "# Score Attribution and EV Findings\n\n" + (
            "\n\n---\n\n".join(sections)
            if sections
            else f"{NO_REAL_DATA_YET} (none of the expected Brief 12/14/15 sections were found in "
            f"{build_report_path}.)"
        )
        return self.export_markdown("05-Research", "Score Attribution and EV Findings", body)

    def export_learning(self, memory_store: MemoryStore) -> Path | None:
        """07-Learning: real pattern_memory stats for every real
        `(setup_type, regime)` combination with at least one real closed
        trade. Currently always empty (zero real trades this project has
        ever closed) -- writes an honest "no real data yet" placeholder
        in that case, never fabricated example content."""
        from learning.pattern_memory import stats_for

        trades = memory_store.recent(memory_type="trade", limit=100_000)
        combos = sorted({(t["payload"].get("setup_type"), t["payload"].get("entry_regime")) for t in trades})
        combos = [c for c in combos if c[0] and c[1]]
        if not combos:
            body = (
                "# Pattern Memory\n\n"
                f"{NO_REAL_DATA_YET} Zero real trades have been closed by this project "
                "as of this export -- learning/pattern_memory.py::stats_for activates automatically "
                "once real trades accumulate, with no further code changes needed.\n"
            )
            return self.export_markdown("07-Learning", "Pattern Memory", body)
        stats = [stats_for(memory_store, setup_type, regime) for setup_type, regime in combos]
        return self.export_markdown("07-Learning", "Pattern Memory", render_pattern_memory(stats))

    def sync_system_docs(self, docs_dir: Path = Path("docs")) -> list[Path]:
        """Part C: copies the real, current content of every real
        `docs/*.md` file into `01-Market-Knowledge/00-System` -- chosen
        over a reference/symlink because an Obsidian vault is commonly
        kept in a directory entirely separate from this git repo, where
        a filesystem reference wouldn't resolve; Obsidian's own linking
        only works within the vault. Copied FRESH every time this runs
        (never cached or written once) -- run this as part of the
        existing daily/manual export path (see main.py) so the vault
        copy can never silently go stale relative to the real source
        files."""
        if self.root is None or not docs_dir.exists():
            return []
        destination = self.root / "01-Market-Knowledge" / "00-System"
        destination.mkdir(parents=True, exist_ok=True)
        written = []
        for source in sorted(docs_dir.glob("*.md")):
            target = destination / source.name
            try:
                shutil.copyfile(source, target)
            except OSError:
                continue
            written.append(target)
        return written


# --- Pure formatters (no I/O) -------------------------------------------


def render_market_knowledge(
    regimes: list[str], open_window_setups: list[str], all_day_setups: list[str], known_gaps: list[str]
) -> str:
    lines = [
        "# Regime and Setup Vocabulary",
        "",
        (
            "Read live from the running code at export time -- see "
            "`intelligence/market_regime.py::Regime` and `execution/live_context.py`'s "
            "`OPEN_WINDOW_SETUPS`/`ALL_DAY_SETUPS`/`KNOWN_GAPS`. Which of these setup "
            "strings currently has real detection logic wired into `_select_setup` can "
            "change independently of this vocabulary list; see that function's own "
            "docstring in `execution/live_context.py` for the current, authoritative "
            "wiring status rather than assuming everything listed here is active."
        ),
        "",
        "## Real regimes (`intelligence/market_regime.py::Regime`)",
        "",
    ]
    lines += [f"- {r}" for r in regimes]
    lines += [
        "",
        "## Real setup types eligible only in the real opening window (`OPEN_WINDOW_SETUPS`)",
        "",
    ]
    lines += [f"- {s}" for s in open_window_setups]
    lines += [
        "",
        "## Real setup types eligible all real session (`ALL_DAY_SETUPS`)",
        "",
    ]
    lines += [f"- {s}" for s in all_day_setups]
    lines += [
        "",
        "## Known real data gaps (`KNOWN_GAPS`)",
        "",
        (
            "Real wiring exists for each of these; the underlying real data source is "
            "still empty/unwired as of this export -- never fabricated, always honestly "
            "0.0/UNAVAILABLE until real data exists."
        ),
        "",
    ]
    lines += [f"- {g}" for g in known_gaps]
    return "\n".join(lines) + "\n"


def render_risk_config(settings: Settings) -> str:
    return (
        "# Current Risk Configuration\n\n"
        "Read live from `Settings` at export time -- never hardcoded here.\n\n"
        f"- **max_risk_per_trade**: {settings.max_risk_per_trade}\n"
        f"- **max_daily_loss**: {settings.max_daily_loss}\n"
        f"- **max_trades_per_day**: {settings.max_trades_per_day}\n"
        f"- **max_position_value**: {settings.max_position_value}\n"
        f"- **entry_slippage_ticks**: {settings.entry_slippage_ticks}\n"
        f"- **exit_slippage_ticks**: {settings.exit_slippage_ticks}\n"
        f"- **tick_size**: {settings.tick_size}\n"
        f"- **capital**: {settings.capital}\n"
        f"- **trading_mode**: {settings.trading_mode}\n"
    )


def render_data_quality(
    archive_status: str, archive_detail: str, gap_check_status: str, field_discovery_summary: list[str]
) -> str:
    lines = [
        "# Data Quality Status",
        "",
        (
            "Every line below is a real, already-computed status passed in by the "
            "caller at export time (Brief 16/18/19's own real functions) -- this note "
            "never recomputes or guesses at any of it."
        ),
        "",
        "## Instrument archive (Brief 18 content validation)",
        "",
        f"- **status**: {archive_status}",
        f"- **detail**: {archive_detail}",
        "",
        "## Missing-archive gap check (Brief 16)",
        "",
        f"- **status**: {gap_check_status}",
        "",
        "## Real field-discovery findings (Brief 19)",
        "",
    ]
    lines += [f"- {line}" for line in field_discovery_summary]
    return "\n".join(lines) + "\n"


def render_pattern_memory(stats: list) -> str:
    lines = [
        "# Pattern Memory",
        "",
        (
            "Real `learning/pattern_memory.py::stats_for` output, one real "
            "`(setup_type, regime)` combination per section."
        ),
        "",
    ]
    for s in stats:
        lines += [
            f"## {s.setup_type} / {s.regime}",
            "",
            f"- **sample_size**: {s.sample_size}",
            f"- **win_rate**: {s.win_rate}",
            f"- **expectancy**: {s.expectancy}",
            (
                f"- **low_confidence**: {s.low_confidence} "
                "(sample_size < learning/pattern_memory.py::MIN_SAMPLES_FOR_CONFIDENCE)"
            ),
            "",
        ]
    return "\n".join(lines)


def render_decision_note(
    attribution: dict[str, Any],
    *,
    validation_reasons: tuple[str, ...] = (),
    outcome: dict[str, Any] | None = None,
    ev_estimate: EVEstimate | None = None,
) -> str:
    """Part B: a readable, organized note built entirely from real,
    already-computed fields -- `attribution` is the exact real
    `score_attribution` dict `execution/live_context.py::_add_candidate`
    already produces (unmodified, no field renamed or dropped);
    `outcome` (real trade P&L/exit fields) is present for a real closed
    trade, absent for a retroactive research candidate that never became
    one; `ev_estimate` is an optional, separately-computed real
    `research/expected_value.py::EVEstimate` the caller may attach."""
    lines = [
        f"# {attribution.get('setup_type')} / {attribution.get('direction')} — {attribution.get('now')}",
        "",
        f"**Regime**: {attribution.get('regime')}  ",
        (
            f"**Confidence**: {attribution.get('confidence')} (threshold {attribution.get('threshold')}, "
            f"cleared: {attribution.get('cleared_threshold')})"
        ),
        "",
        "## Score attribution (7 components, real)",
        "",
        f"- **technical_score**: {attribution.get('technical_score')}",
        f"- **opening_score**: {attribution.get('opening_score')}",
        f"- **volume_score**: {attribution.get('volume_score')} ({attribution.get('volume_reason')})",
        f"- **option_score**: {attribution.get('option_score')} ({attribution.get('option_reason')})",
        f"- **global_score**: {attribution.get('global_score')} (direction: {attribution.get('global_direction')})",
        f"- **news_score**: {attribution.get('news_score')} (direction: {attribution.get('news_direction')})",
        f"- **risk_penalty**: {attribution.get('risk_penalty')}",
        "",
        f"**Setup evidence**: {attribution.get('setup_evidence')}",
        "",
        (
            f"**Real data completeness**: {attribution.get('data_completeness')}% "
            f"({attribution.get('data_available')})"
        ),
    ]
    if validation_reasons:
        lines += ["", "## Validator reasoning (real)", ""]
        lines += [f"- {reason}" for reason in validation_reasons]
    if outcome is not None:
        lines += ["", "## Real outcome", ""]
        lines += [f"- **{key}**: {value}" for key, value in outcome.items()]
    if ev_estimate is not None:
        lines += [
            "",
            "## Real Expected Value (measurement only -- see research/expected_value.py)",
            "",
            f"- **ev_source**: {ev_estimate.ev_source}",
            f"- **sample_size**: {ev_estimate.sample_size}",
            f"- **win_rate**: {ev_estimate.win_rate}",
            f"- **ev_r**: {ev_estimate.ev_r}",
        ]
        decomposition = ev_estimate.decomposition()
        if decomposition is not None:
            lines += [
                f"- **win_contribution**: +{decomposition.win_contribution:.3f}R",
                f"- **loss_contribution**: -{decomposition.loss_contribution:.3f}R",
                f"- **costs**: -{decomposition.costs:.3f}R",
                f"- **slippage**: -{decomposition.slippage:.3f}R",
                f"- **dominant_driver**: {decomposition.dominant_driver()}",
            ]
    return "\n".join(lines) + "\n"


def _extract_report_sections(path: Path, headings: tuple[str, ...]) -> list[str]:
    """Real, exact extraction: each returned string is the verbatim real
    text from `path` starting at one of `headings` up to (not including)
    the next `## ` heading at the same level -- a copy, never a
    paraphrase, so there is no way for this to drift from the real
    source content it was copied from."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    sections = []
    for heading in headings:
        try:
            start = next(i for i, line in enumerate(lines) if line.startswith(heading))
        except StopIteration:
            continue
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if lines[i].startswith("## "):
                end = i
                break
        sections.append("\n".join(lines[start:end]).rstrip())
    return sections
