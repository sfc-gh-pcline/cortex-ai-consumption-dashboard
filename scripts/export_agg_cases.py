"""Export aggregation parity cases from the pandas implementation.

``web/lib/agg`` must reproduce ``utils/agg.py`` and the formatters in
``utils/ui.py`` number for number. This script feeds small synthetic frames
through the Python functions and writes inputs and outputs to
``tests/fixtures/agg_cases.json``; ``web/tests/agg-parity.test.ts`` replays them.

Run from the repository root after any aggregation change::

    uv run python scripts/export_agg_cases.py

Offline: no Snowflake connection is opened.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import agg, ui  # noqa: E402
from utils.date_ranges import resolve_periods  # noqa: E402
from utils.view_state import (  # noqa: E402
    ComparisonPeriods,
    CoverageState,
    DateWindow,
    FilterSelection,
    SourceCoverage,
)

OUTPUT = ROOT / "tests" / "fixtures" / "agg_cases.json"


def scalar(value: object) -> object:
    """Convert pandas scalars to JSON-safe values (NaN becomes null, dates become ISO)."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat() if value == value.normalize() else value.isoformat()
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if hasattr(value, "item"):
        return scalar(value.item())
    if isinstance(value, (list, tuple)):
        return [scalar(v) for v in value]
    return value


def records(frame: pd.DataFrame) -> list[dict]:
    """Serialize a frame row by row."""
    return [{k: scalar(v) for k, v in row.items()} for row in frame.to_dict("records")]


def frame_json(frame: pd.DataFrame) -> dict:
    """Serialize a frame with its column order preserved."""
    return {"columns": list(frame.columns), "rows": records(frame)}


def window_json(window: DateWindow | None) -> dict | None:
    """Serialize a DateWindow."""
    return (
        None
        if window is None
        else {"start": window.start.isoformat(), "end": window.end.isoformat()}
    )


def coverage_json(coverage: SourceCoverage) -> dict:
    """Serialize a SourceCoverage in the web app's camelCase shape."""
    data = asdict(coverage)
    return {
        "dependency": data["dependency"],
        "actualSource": data["actual_source"],
        "fetchedAt": scalar(data["fetched_at"]),
        "requested": window_json(coverage.requested),
        "served": window_json(coverage.served),
        "retentionStart": scalar(data["retention_start"]),
        "latencyHours": data["latency_hours"],
        "snapshotAt": scalar(data["snapshot_at"]),
        "state": coverage.state.value,
        "reason": data["reason"],
        "readStartedAt": scalar(data["read_started_at"]),
        "completeThrough": scalar(data["complete_through"]),
    }


def periods_json(periods: ComparisonPeriods) -> dict:
    """Serialize ComparisonPeriods."""
    return {
        "requested": window_json(periods.requested),
        "current": window_json(periods.current),
        "previous": window_json(periods.previous),
        "partialToday": window_json(periods.partial_today),
    }


def make_facts() -> pd.DataFrame:
    """The tests/test_agg.py fact frame."""
    return pd.DataFrame(
        {
            "USAGE_DATE": ["2026-08-01", "2026-08-01", "2026-08-02"],
            "SURFACE": ["CoCo", "CoWork", "CoCo"],
            "USER_NAME": ["ALICE", "BOB", "ALICE"],
            "CREDITS": [1.0, 2.0, 3.0],
            "TOKENS": [10, 20, 30],
            "CALLS": [1, 1, 2],
        }
    )


PERIODS = resolve_periods(dt.date(2026, 9, 20), "7d")
assert PERIODS.previous is not None and PERIODS.current is not None
WINDOW = DateWindow(PERIODS.previous.start, PERIODS.current.end)
COVERAGE = SourceCoverage(
    "CoCo",
    "direct",
    dt.datetime(2026, 9, 20, 8, tzinfo=dt.UTC),
    WINDOW,
    WINDOW,
    retention_start=dt.date(2025, 9, 21),
    state=CoverageState.AVAILABLE,
    complete_through=dt.date(2026, 9, 19),
)


def period_facts(previous: float = 2.0, current: float = 2.0) -> pd.DataFrame:
    """Fourteen days of constant spend across both comparison periods."""
    return pd.DataFrame(
        {
            "USAGE_DATE": [d.date().isoformat() for d in pd.date_range(WINDOW.start, WINDOW.end)],
            "CREDITS": [previous] * 7 + [current] * 7,
            "USER_NAME": ["Alice"] * 14,
        }
    )


def partition_cases() -> list[dict]:
    """Every combination from tests/test_view_state.py plus the named edge cases."""
    grid_frame = pd.DataFrame(
        {
            "ROW_ID": range(6),
            "SURFACE": ["CoCo", "CoCo", "CoCo", "Cortex Search", "Cortex Agents", "CoWork"],
            "USER_NAME": ["Alice", "Bob", None, None, "Alice", "Bob"],
            "MODEL_NAME": ["model", None, "other", "model", None, None],
            "CREDITS": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    cases = []
    for users, models, tag_users in itertools.product(
        [(), ("Alice",), ("Bob",)], [(), ("model",), ("other",)], [None, [], ["Alice"]]
    ):
        result = agg.apply_filters(grid_frame, users, models, tag_users)
        cases.append(
            {
                "name": f"grid users={list(users)} models={list(models)} tags={tag_users}",
                "frame": records(grid_frame),
                "users": list(users),
                "models": list(models),
                "tag_users": tag_users,
                "tags": [],
                "source": None,
                "page_values": {},
                "expected": {
                    "matched": result.matched["ROW_ID"].tolist(),
                    "unscoped": result.unscoped["ROW_ID"].tolist(),
                    "excluded": result.excluded["ROW_ID"].tolist(),
                },
            }
        )

    def named(
        name, frame, *, users=(), models=(), tag_users=None, tags=(), source=None, page_values=None
    ):
        frame = frame.copy()
        frame.insert(0, "ROW_ID", range(len(frame)))
        result = agg.partition_scope(
            frame,
            FilterSelection(
                users=tuple(users),
                models=tuple(models),
                tags=tuple(tags),
                page_values={k: tuple(v) for k, v in (page_values or {}).items()},
            ),
            source=source,
            tag_users=tag_users,
        )
        return {
            "name": name,
            "frame": records(frame),
            "users": list(users),
            "models": list(models),
            "tag_users": tag_users,
            "tags": list(tags),
            "source": source,
            "page_values": {k: list(v) for k, v in (page_values or {}).items()},
            "expected": {
                "matched": result.matched["ROW_ID"].tolist(),
                "unscoped": result.unscoped["ROW_ID"].tolist(),
                "excluded": result.excluded["ROW_ID"].tolist(),
            },
        }

    cases.append(
        named(
            "known mismatch wins over unknown model",
            pd.DataFrame({"USER_NAME": ["Bob"], "MODEL_NAME": [None], "CREDITS": [7.0]}),
            users=["Alice"],
            models=["model"],
            source="CoCo",
        )
    )
    cases.append(
        named(
            "active unresolved tags are not inactive",
            pd.DataFrame({"USER_NAME": ["Alice"], "CREDITS": [2.0]}),
            tags=["team = engineering"],
            source="CoCo",
        )
    )
    cases.append(
        named(
            "local mismatch excludes unknown global dimension",
            pd.DataFrame({"USER_NAME": [None], "ROLE_NAME": ["ADMIN"], "CREDITS": [2.0]}),
            users=["Alice"],
            source="CoCo",
            page_values={"ROLE_NAME": ["ANALYST"]},
        )
    )
    cases.append(
        named(
            "warehouse source overrides classified surface capability",
            pd.DataFrame({"SURFACE": ["CoCo"], "USER_NAME": ["Alice"], "CREDITS": [2.0]}),
            users=["Alice"],
            source="Warehouse Attribution",
        )
    )
    cases.append(
        named(
            "missing and unknown values are not explicit matches",
            pd.DataFrame({"USER_NAME": [None, "(unknown)", ""], "CREDITS": [1.0, 2.0, 3.0]}),
            users=["(unknown)"],
            source="CoCo",
        )
    )
    cases.append(
        named(
            "model filter for a model absent from the page",
            pd.DataFrame(
                {"USER_NAME": ["ALICE"], "MODEL_NAME": ["claude-opus-4-6"], "CREDITS": [1.0]}
            ),
            models=["claude-haiku-4-5"],
        )
    )
    cases.append(
        named(
            "page values on a supported column",
            pd.DataFrame(
                {
                    "USER_NAME": ["A", "B", "C"],
                    "ROLE_NAME": ["X", "Y", None],
                    "CREDITS": [1.0, 2.0, 3.0],
                }
            ),
            source="CoCo",
            page_values={"ROLE_NAME": ["X"]},
        )
    )
    return cases


def by_dimension_cases() -> list[dict]:
    """Breakdown cases from tests/test_agg.py."""
    facts = make_facts()
    nulled = facts.copy()
    nulled.loc[0, "SURFACE"] = None
    warehouse = pd.DataFrame(
        {
            "SURFACE": ["CoCo", "CoCo", "CoWork"],
            "WAREHOUSE_CREDITS": [1.5, 2.5, 0.5],
            "COMPUTE_CREDITS": [1.5, 2.5, 0.5],
        }
    )
    inputs = [
        ("facts by surface", facts, "SURFACE", "SURFACE", None),
        ("without user column", facts.drop(columns=["USER_NAME"]), "SURFACE", "SURFACE", None),
        ("without tokens", facts.drop(columns=["TOKENS"]), "SURFACE", "SURFACE", None),
        (
            "explicit warehouse metrics",
            warehouse,
            "SURFACE",
            "SURFACE",
            ["WAREHOUSE_CREDITS", "COMPUTE_CREDITS"],
        ),
        ("null relabelled unknown", nulled, "SURFACE", "SURFACE", None),
        ("empty frame", pd.DataFrame(), "SURFACE", "SURFACE", None),
        ("missing group column", pd.DataFrame({"OTHER": [1]}), "SURFACE", "SURFACE", None),
        (
            "no metric present",
            pd.DataFrame({"SURFACE": ["CoCo"], "SOMETHING_ELSE": [1]}),
            "SURFACE",
            "SURFACE",
            None,
        ),
        ("default label column", facts, "SURFACE", "DIMENSION", None),
        ("by user", facts, "USER_NAME", "USER_NAME", None),
    ]
    return [
        {
            "name": name,
            "frame": frame_json(frame),
            "column": column,
            "label_col": label_col,
            "metrics": metrics,
            "expected": frame_json(
                agg.by_dimension(frame, column, label_col=label_col, metrics=metrics)
            ),
        }
        for name, frame, column, label_col, metrics in inputs
    ]


def compare_cases() -> list[dict]:
    """Equal-period comparison cases from tests/test_comparisons.py."""
    unrated = period_facts()
    unrated["IS_UNRATED"] = 0
    unrated.loc[0, "IS_UNRATED"] = 1
    partial_rate = period_facts(1, 2)
    partial_rate["IS_UNRATED"] = 0
    partial_rate["RATE_INCOMPLETE"] = False
    partial_rate.loc[0, "RATE_INCOMPLETE"] = True
    with_today = pd.concat(
        [period_facts(), pd.DataFrame({"USAGE_DATE": ["2026-09-20"], "CREDITS": [999.0]})],
        ignore_index=True,
    )
    inputs = [
        ("constant spend", period_facts(), [COVERAGE], "CREDITS"),
        ("both zero", period_facts(0, 0), [COVERAGE], "CREDITS"),
        ("zero baseline", period_facts(0, 1), [COVERAGE], "CREDITS"),
        ("to zero", period_facts(1, 0), [COVERAGE], "CREDITS"),
        (
            "unknown component",
            period_facts(),
            [COVERAGE, replace(COVERAGE, state=CoverageState.UNKNOWN)],
            "CREDITS",
        ),
        (
            "incomplete component",
            period_facts(),
            [COVERAGE, replace(COVERAGE, complete_through=dt.date(2026, 9, 18))],
            "CREDITS",
        ),
        (
            "retention component",
            period_facts(),
            [COVERAGE, replace(COVERAGE, retention_start=dt.date(2026, 9, 10))],
            "CREDITS",
        ),
        (
            "unavailable component",
            period_facts(1, 2),
            [COVERAGE, replace(COVERAGE, state=CoverageState.UNAVAILABLE)],
            "CREDITS",
        ),
        ("missing rate", unrated, [COVERAGE], "CREDITS"),
        ("partial token type rate", partial_rate, [COVERAGE], "CREDITS"),
        ("today excluded", with_today, [COVERAGE], "CREDITS"),
        ("no coverage", period_facts(), [], "CREDITS"),
        ("metric absent", period_facts(), [COVERAGE], "TOKENS"),
        (
            "warehouse metric",
            period_facts(1, 2).rename(columns={"CREDITS": "WAREHOUSE_CREDITS"}),
            [replace(COVERAGE, dependency="Warehouse Attribution")],
            "WAREHOUSE_CREDITS",
        ),
    ]
    cases = []
    for name, frame, coverages, metric in inputs:
        result = agg.compare_periods(frame, PERIODS, coverages, metric)
        cases.append(
            {
                "name": name,
                "frame": frame_json(frame),
                "coverages": [coverage_json(c) for c in coverages],
                "value_col": metric,
                "expected": {
                    "current": result.current,
                    "previous": result.previous,
                    "deltaPercent": result.delta_percent,
                    "state": result.state.value,
                    "reason": result.reason,
                },
            }
        )
    none_result = agg.compare_periods(None, PERIODS, [COVERAGE])
    cases.append(
        {
            "name": "frame unavailable",
            "frame": None,
            "coverages": [coverage_json(COVERAGE)],
            "value_col": "CREDITS",
            "expected": {
                "current": none_result.current,
                "previous": none_result.previous,
                "deltaPercent": none_result.delta_percent,
                "state": none_result.state.value,
                "reason": none_result.reason,
            },
        }
    )
    return cases


def calendar_cases() -> list[dict]:
    """Calendar completion with and without verified day coverage."""
    sparse = period_facts().iloc[[7, 9]]
    partial = replace(COVERAGE, state=CoverageState.PARTIAL, complete_through=dt.date(2026, 9, 18))
    nulls = period_facts().iloc[[7, 8]].copy()
    nulls.loc[nulls.index[0], "CREDITS"] = None
    inputs = [
        ("partial coverage", sparse, PERIODS.current, [partial]),
        ("no coverage", sparse, PERIODS.current, []),
        ("full coverage", sparse, PERIODS.current, [COVERAGE]),
        ("null observation stays null", nulls, PERIODS.current, [COVERAGE]),
        ("empty frame", period_facts().iloc[:0], PERIODS.current, [COVERAGE]),
    ]
    return [
        {
            "name": name,
            "frame": frame_json(frame),
            "window": window_json(window),
            "coverages": [coverage_json(c) for c in coverages],
            "value_col": "CREDITS",
            "expected": records(agg.calendar_daily(frame, window, coverages)),
        }
        for name, frame, window, coverages in inputs
    ]


def daily_cases() -> list[dict]:
    """Daily aggregation, grouped and calendar-completed."""
    gapped = period_facts().iloc[[7, 9]].copy()
    gapped["TOKENS"] = 10
    gapped["CALLS"] = 1
    grouped = period_facts()
    grouped["SURFACE"] = ["CoCo"] * 7 + ["Cortex Search"] * 7
    grouped["TOKENS"] = 10
    grouped["CALLS"] = 1
    search_unknown = replace(
        COVERAGE, dependency="Cortex Search — Daily", state=CoverageState.UNKNOWN
    )
    empty_sub = period_facts().iloc[:0].copy()
    empty_sub["SUB_SURFACE"] = pd.Series(dtype=str)
    empty_sub["TOKENS"] = pd.Series(dtype=float)
    empty_sub["CALLS"] = pd.Series(dtype=float)
    inputs = [
        ("plain grouped by surface", make_facts(), "SURFACE", None),
        ("plain ungrouped", make_facts(), None, None),
        (
            "gaps without coverage",
            gapped,
            None,
            {"display_window": PERIODS.current, "source_coverages": ()},
        ),
        (
            "gaps with coverage",
            gapped,
            None,
            {"display_window": PERIODS.current, "source_coverages": (COVERAGE,)},
        ),
        (
            "grouped by surface with per-source evidence",
            agg.period_slice(grouped, PERIODS.current),
            "SURFACE",
            {
                "display_window": PERIODS.current,
                "source_coverages": (COVERAGE, search_unknown),
                "series_values": {"SURFACE": ("CoCo", "Cortex Search")},
            },
        ),
        (
            "empty grouped calendar",
            empty_sub,
            "SUB_SURFACE",
            {"display_window": PERIODS.current, "source_coverages": ()},
        ),
    ]
    cases = []
    for name, frame, color_col, attrs in inputs:
        frame = frame.copy()
        if attrs:
            frame.attrs.update(attrs)
        result = agg.daily(frame, color_col)
        trend = agg.daily_trend(frame) if attrs else None
        cases.append(
            {
                "name": name,
                "frame": frame_json(frame),
                "color_col": color_col,
                "context": None
                if not attrs
                else {
                    "displayWindow": window_json(attrs["display_window"]),
                    "coverages": [coverage_json(c) for c in attrs.get("source_coverages", ())],
                    "seriesValues": {k: list(v) for k, v in attrs.get("series_values", {}).items()},
                },
                "expected": frame_json(result),
                "expected_trend": trend,
            }
        )
    return cases


def misc_cases() -> dict:
    """Totals, options, token mix, tags, warehouse split, relative periods, and trend delta."""
    facts = make_facts()
    tokens = pd.DataFrame(
        {
            "TOK_INPUT": [10, 5],
            "TOK_OUTPUT": [3, 0],
            "TOK_CACHE_READ_INPUT": [0, 0],
            "TOK_CACHE_WRITE_INPUT": [1, 1],
        }
    )
    tags = pd.DataFrame(
        {
            "TAG_NAME": ["team", "team", "cost_center"],
            "TAG_VALUE": ["eng", "eng", "42"],
            # CAROL has no facts, so the tag groups never tie on credits: pandas'
            # sort is not stable and a tie would make row order implementation-defined.
            "TAG_LABEL": ["team = eng", "team = eng", "cost_center = 42"],
            "USER_NAME": ["ALICE", "CAROL", "CAROL"],
        }
    )
    agents = pd.DataFrame(
        {
            "USER_NAME": ["A", "B", "C"],
            "AGENT_TAG_LABELS": ["env = prod|team = x", "", None],
            "CREDITS": [1.0, 2.0, 4.0],
            "TOKENS": [1, 2, 4],
            "CALLS": [1, 1, 1],
        }
    )
    warehouse = pd.DataFrame(
        {
            "SURFACE": ["CoCo", "CoCo", "CoWork"],
            "TIER": ["per_request", "surface", "per_request"],
            "WAREHOUSE_CREDITS": [1.5, 0.5, 2.0],
        }
    )
    current = pd.DataFrame(
        {"USAGE_DATE": ["2026-09-19", "2026-09-18", "2026-09-18"], "CREDITS": [2.0, 1.0, None]}
    )
    previous = pd.DataFrame({"USAGE_DATE": ["2026-09-16", "2026-09-17"], "CREDITS": [3.0, 4.0]})
    relative = agg.relative_period_daily(current, previous)
    comparison = agg.compare_periods(period_facts(1, 2), PERIODS, [COVERAGE])
    return {
        "totals": [
            {"name": "facts", "frame": frame_json(facts), "expected": agg.totals(facts)},
            {
                "name": "no users",
                "frame": frame_json(facts.drop(columns=["USER_NAME"])),
                "expected": agg.totals(facts.drop(columns=["USER_NAME"])),
            },
            {
                "name": "empty",
                "frame": frame_json(pd.DataFrame()),
                "expected": agg.totals(pd.DataFrame()),
            },
        ],
        "options": [
            {
                "frame": frame_json(facts),
                "column": "USER_NAME",
                "expected": agg.options(facts, "USER_NAME"),
            },
            {"frame": frame_json(facts), "column": "NOPE", "expected": agg.options(facts, "NOPE")},
        ],
        "token_mix": [
            {"frame": frame_json(tokens), "expected": frame_json(agg.token_mix(tokens))},
            {
                "frame": frame_json(pd.DataFrame()),
                "expected": frame_json(agg.token_mix(pd.DataFrame())),
            },
        ],
        "tag_label_options": {"tags": frame_json(tags), "expected": agg.tag_label_options(tags)},
        "users_for_tags": {
            "tags": frame_json(tags),
            "selected": ["team = eng"],
            "expected": agg.users_for_tags(tags, ["team = eng"]),
        },
        "by_user_tag": {
            "frame": frame_json(facts),
            "tags": frame_json(tags),
            "expected": frame_json(agg.by_user_tag(facts, tags)),
        },
        "by_agent_tag": {
            "frame": frame_json(agents),
            "expected": frame_json(agg.by_agent_tag(agents)),
        },
        "warehouse_surface": [
            {
                "frame": frame_json(warehouse),
                "surface": "CoCo",
                "expected": agg.warehouse_credits_for_surface(warehouse, "CoCo"),
            },
            {
                "frame": frame_json(warehouse),
                "surface": "Cortex Agents",
                "expected": agg.warehouse_credits_for_surface(warehouse, "Cortex Agents"),
            },
        ],
        "relative": {
            "current": frame_json(current),
            "previous": frame_json(previous),
            "expected": frame_json(relative),
        },
        "trend_delta": {
            "comparison": {
                "current": comparison.current,
                "previous": comparison.previous,
                "deltaPercent": comparison.delta_percent,
                "state": comparison.state.value,
                "reason": comparison.reason,
            },
            "expected": f"{comparison.delta_percent:+.1f}% ({comparison.current - comparison.previous:+.4f})",
        },
        "leader": {
            "frame": frame_json(agg.by_dimension(facts, "SURFACE", label_col="SURFACE")),
            "total": 6.0,
            "expected": {"label": "CoCo", "share": 4.0 / 6.0},
        },
    }


def format_cases() -> dict:
    """Formatter outputs from utils/ui.py."""
    ints = [0, 1, 999, 1000, 1234567, 12.9, -5, None, float("nan")]
    credits = [
        (0, 2),
        (0.004, 2),
        (0.01, 2),
        (1234.5678, 2),
        (0.00000123, 8),
        (None, 2),
        (float("nan"), 2),
        (-1.5, 2),
    ]
    tokens = [0, 999, 1000, 12345, 340_500_000, 1_200_000_000, 2.5e12, -1500, None]
    return {
        "fmt_int": [[scalar(v), ui.fmt_int(v)] for v in ints],
        "fmt_credits": [[scalar(v), d, ui.fmt_credits(v, d)] for v, d in credits],
        "fmt_tokens": [[scalar(v), ui.fmt_tokens(v)] for v in tokens],
    }


def main() -> int:
    """Write the fixture and report its path."""
    document = {
        "periods": periods_json(PERIODS),
        "partition": partition_cases(),
        "by_dimension": by_dimension_cases(),
        "compare": compare_cases(),
        "calendar": calendar_cases(),
        "daily": daily_cases(),
        **misc_cases(),
        "format": format_cases(),
    }
    OUTPUT.write_text(json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
