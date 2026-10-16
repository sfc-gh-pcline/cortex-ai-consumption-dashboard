"""Pure view-contract checks; no connections or live account data."""

import pickle
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from utils import queries as q
from utils.agg import apply_filters, partition_scope
from utils.view_state import (
    FILTER_CAPABILITIES,
    PAGES,
    CoverageState,
    DateWindow,
    FilterSelection,
    LoadedDependency,
    SourceCoverage,
)


def test_pages_preserve_all_dependency_keys_and_routes():
    assert len(PAGES) == 11
    assert {page.dependency_key for page in PAGES} == set(q.PAGE_REFRESH_PLANS)
    assert len({page.url_path for page in PAGES}) == 11
    assert PAGES[0].url_path == ""
    root = Path(__file__).resolve().parents[1]
    assert all((root / page.path).is_file() for page in PAGES)
    assert sum(page.group == "AI Services" for page in PAGES) == 6


def test_window_rejects_reversed_dates():
    with pytest.raises(ValueError, match="Window end"):
        DateWindow(date(2026, 9, 20), date(2026, 9, 19))
    assert DateWindow(date(2026, 9, 1), date(2026, 9, 7)).days == 7


def test_selection_owns_immutable_values():
    values = {"ROLE_NAME": ["ANALYST"]}
    selection = FilterSelection(page_values=values)  # ty: ignore[invalid-argument-type]
    values["ROLE_NAME"].append("ADMIN")
    assert selection.page_values["ROLE_NAME"] == ("ANALYST",)
    assert selection.active
    assert not FilterSelection().active
    with pytest.raises(TypeError):
        selection.page_values["ROLE_NAME"] = ("ADMIN",)  # ty: ignore[invalid-assignment]


def test_source_evidence_survives_cache_serialization():
    window = DateWindow(date(2026, 9, 1), date(2026, 9, 7))
    coverage = SourceCoverage(
        "CoCo", "direct", datetime.now(UTC), window, window, state=CoverageState.AVAILABLE
    )
    result = LoadedDependency(pd.DataFrame({"CREDITS": [1.5]}), coverage)
    restored = pickle.loads(pickle.dumps(result))
    assert restored.coverage == coverage
    pd.testing.assert_frame_equal(restored.frame, result.frame)


def test_capabilities_do_not_invent_attribution():
    assert "MODEL_NAME" not in FILTER_CAPABILITIES[q.SURFACE_AGENTS]
    assert "MODEL_NAME" not in FILTER_CAPABILITIES[q.SURFACE_COWORK]
    assert "USER_NAME" not in FILTER_CAPABILITIES[q.SURFACE_SEARCH]
    assert "USER_NAME" not in FILTER_CAPABILITIES[q.SURFACE_WAREHOUSE_ATTRIBUTION]


@pytest.mark.parametrize("users", [(), ("Alice",), ("Bob",)])
@pytest.mark.parametrize("models", [(), ("model",), ("other",)])
@pytest.mark.parametrize("tag_users", [None, [], ["Alice"]])
def test_partitions_reconcile_with_combined_filters(users, models, tag_users):
    frame = pd.DataFrame(
        {
            "ROW_ID": range(6),
            "SURFACE": ["CoCo", "CoCo", "CoCo", "Cortex Search", "Cortex Agents", "CoWork"],
            "USER_NAME": ["Alice", "Bob", None, None, "Alice", "Bob"],
            "MODEL_NAME": ["model", None, "other", "model", None, None],
            "CREDITS": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        },
        index=pd.Index([0, 0, 1, 1, 2, 2]),
    )
    original = frame.copy(deep=True)
    result = apply_filters(frame, users, models, tag_users)
    combined = pd.concat([result.matched, result.unscoped, result.excluded])
    assert sorted(combined.ROW_ID) == list(range(6))
    assert combined.CREDITS.sum() == 21.0
    pd.testing.assert_frame_equal(frame, original)


def test_known_mismatch_wins_over_unknown_model():
    frame = pd.DataFrame({"USER_NAME": ["Bob"], "MODEL_NAME": [None], "CREDITS": [7.0]})
    result = apply_filters(frame, ["Alice"], ["model"], source="CoCo")
    assert result.excluded.CREDITS.sum() == 7.0
    assert result.unscoped.empty
    assert result.matched.empty


def test_active_unresolved_tags_are_not_inactive():
    frame = pd.DataFrame({"USER_NAME": ["Alice"], "CREDITS": [2.0]})
    result = partition_scope(frame, FilterSelection(tags=("team = engineering",)), source="CoCo")
    assert result.matched.empty
    assert result.unscoped.CREDITS.sum() == 2.0


def test_local_mismatch_excludes_unknown_global_dimension():
    frame = pd.DataFrame({"USER_NAME": [None], "ROLE_NAME": ["ADMIN"], "CREDITS": [2.0]})
    result = apply_filters(frame, ["Alice"], source="CoCo", page_values={"ROLE_NAME": ["ANALYST"]})
    assert result.excluded.CREDITS.sum() == 2.0
    assert result.unscoped.empty


def test_warehouse_source_overrides_classified_surface_capability():
    frame = pd.DataFrame({"SURFACE": ["CoCo"], "USER_NAME": ["Alice"], "CREDITS": [2.0]})
    result = apply_filters(frame, ["Alice"], source="Warehouse Attribution")
    assert result.matched.empty
    assert result.unscoped.CREDITS.sum() == 2.0


def test_missing_and_unknown_values_are_not_explicit_matches():
    frame = pd.DataFrame({"USER_NAME": [None, "(unknown)", ""], "CREDITS": [1.0, 2.0, 3.0]})
    result = apply_filters(frame, ["(unknown)"], source="CoCo")
    assert result.matched.empty
    assert result.unscoped.CREDITS.sum() == 6.0
