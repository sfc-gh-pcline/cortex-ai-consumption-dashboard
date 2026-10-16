"""Cover the pure label helpers in :mod:`utils.ui`.

Rendering needs a Streamlit runtime, but the label resolution these tests exercise
is ordinary Python, so it can be checked offline.
"""

from __future__ import annotations

import pandas as pd
import pytest
import streamlit as st

from utils import ui
from utils.ui import LoadState, infer_state

WAREHOUSE_COLUMNS = {
    "WAREHOUSE_CREDITS": st.column_config.NumberColumn("Total credits", format="%.6f"),
    "COMPUTE_CREDITS": st.column_config.NumberColumn("Compute", format="%.6f"),
}


@pytest.mark.parametrize("formatter", [ui.fmt_int, ui.fmt_credits, ui.fmt_tokens])
def test_missing_values_are_not_zero(formatter):
    assert formatter(None) == "Unavailable"
    assert formatter(float("nan")) == "Unavailable"
    assert formatter(0) in {"0", "0.00"}


def test_small_positive_credits_remain_visible():
    assert ui.fmt_credits(0.001) == "<0.01"
    assert ui.fmt_credits(0.01) == "0.01"
    assert ui.fmt_credits(0.00001, decimals=6) == "0.000010"


def test_share_denominator_precedes_truncation(monkeypatch):
    captured = {}
    monkeypatch.setattr(st, "caption", lambda *_args: None)
    monkeypatch.setattr(
        st, "dataframe", lambda data, **kwargs: captured.update(data=data, **kwargs)
    )
    frame = pd.DataFrame({"NAME": ["A", "B", "C"], "CREDITS": [60.0, 30.0, 10.0]})
    ui.breakdown_table(frame, "Name", limit=1)
    assert captured["data"]["SHARE"].tolist() == [0.6, 0.4]
    assert captured["data"]["NAME"].tolist() == ["A", "Other"]
    assert captured["column_config"]["SHARE"]["type_config"]["max_value"] == 1.0
    ui.breakdown_table(frame.head(1), "Name", denominator=200.0)
    assert captured["data"]["SHARE"].tolist() == [0.3]


def test_other_never_sums_distinct_users(monkeypatch):
    captured = {}
    monkeypatch.setattr(st, "caption", lambda *_args: None)
    monkeypatch.setattr(
        st, "dataframe", lambda data, **kwargs: captured.update(data=data, **kwargs)
    )
    frame = pd.DataFrame(
        {"NAME": ["A", "B", "C"], "CREDITS": [60.0, 30.0, 10.0], "USERS": [2, 2, 2]}
    )
    ui.breakdown_table(frame, "Name", limit=1)
    assert pd.isna(captured["data"].iloc[1]["USERS"])
    assert captured["data"]["CREDITS"].sum() == 100.0


def test_unavailable_warehouse_is_not_displayed_as_zero(monkeypatch):
    from utils import queries as q

    values = []
    monkeypatch.setattr(st, "metric", lambda _label, value: values.append(value))
    monkeypatch.setattr(st, "subheader", lambda *_args: None)
    monkeypatch.setattr(st, "caption", lambda *_args: None)
    monkeypatch.setattr(st, "warning", lambda *_args: None)
    ui.warehouse_component(q.WarehouseFactsResult(pd.DataFrame(), True, "Unavailable"), "CoCo")
    assert values == ["Unavailable"]


class TestResourceStatusColor:
    @pytest.mark.parametrize("status", ["Active", "Configured", "Created"])
    def test_healthy_statuses_are_green(self, status):
        assert ui.resource_status_color(status) == "#00843D"

    def test_suspended_status_is_amber(self):
        assert ui.resource_status_color("Suspended") == "#B54708"

    def test_not_created_status_is_red(self):
        assert ui.resource_status_color("Not created") == "#B42318"

    def test_unknown_status_is_neutral(self):
        assert ui.resource_status_color("ACCOUNT_USAGE only") == "#667085"


class TestLoadingQuerySummary:
    def test_distinguishes_parallel_and_dependent_queries(self):
        assert ui.loading_query_summary(12) == (
            "Running 12 independent queries in parallel, then 1 dependent query (13 total)..."
        )


class TestShareLabel:
    """The share bar names its metric using that column's own configured header."""

    @pytest.mark.parametrize(
        ("column", "expected"),
        [("CREDITS", "credits"), ("TOKENS", "tokens"), ("CALLS", "usage records")],
    )
    def test_reads_the_standard_metric_labels(self, column, expected):
        assert ui._share_label(column, {}) == expected

    @pytest.mark.parametrize(
        ("column", "expected"),
        [("WAREHOUSE_CREDITS", "total credits"), ("COMPUTE_CREDITS", "compute")],
    )
    def test_reads_page_specific_labels(self, column, expected):
        """Without this the Warehouse page headed a column "share of warehouse_credits"."""
        assert ui._share_label(column, WAREHOUSE_COLUMNS) == expected

    def test_page_config_wins_over_the_standard_config(self):
        override = {"CREDITS": st.column_config.NumberColumn("Attributed credits")}
        assert ui._share_label("CREDITS", override) == "attributed credits"

    def test_falls_back_to_the_prettified_column_name(self):
        assert ui._share_label("SOME_OTHER_METRIC", {}) == "some other metric"

    @pytest.mark.parametrize(
        "config",
        ["not-a-config", None, {}, {"label": ""}, {"label": 7}],
        ids=["string", "none", "no-label", "blank-label", "non-string-label"],
    )
    def test_falls_back_rather_than_raising_on_an_unexpected_config(self, config):
        """A header is not worth a broken page if Streamlit changes this shape."""
        assert ui._share_label("METRIC_NAME", {"METRIC_NAME": config}) == "metric name"


# ---------------------------------------------------------------------------
# LoadState / infer_state
# ---------------------------------------------------------------------------


class TestInferState:
    """infer_state returns the correct LoadState from pre-filter DataFrame and flag."""

    def test_source_unavailable_when_raw_is_none(self):
        assert infer_state(None) == LoadState.SOURCE_UNAVAILABLE

    def test_no_usage_when_raw_is_empty_no_filters(self):
        assert infer_state(pd.DataFrame()) == LoadState.NO_USAGE

    def test_no_usage_when_raw_is_empty_with_filters(self):
        """Empty source means NO_USAGE even if filters are active."""
        assert infer_state(pd.DataFrame(), active_filters=True) == LoadState.NO_USAGE

    def test_filtered_empty_when_raw_has_rows_and_filters_active(self):
        df = pd.DataFrame({"CREDITS": [1.0, 2.0]})
        assert infer_state(df, active_filters=True) == LoadState.FILTERED_EMPTY

    def test_no_usage_when_raw_has_rows_but_no_filters(self):
        """Raw had rows but no filter is active — still treated as NO_USAGE."""
        df = pd.DataFrame({"CREDITS": [1.0]})
        assert infer_state(df, active_filters=False) == LoadState.NO_USAGE

    @pytest.mark.parametrize(
        ("raw_empty", "active_filters", "expected"),
        [
            (True, False, LoadState.NO_USAGE),
            (True, True, LoadState.NO_USAGE),
            (False, True, LoadState.FILTERED_EMPTY),
            (False, False, LoadState.NO_USAGE),
        ],
        ids=["empty-no-filter", "empty-with-filter", "rows-with-filter", "rows-no-filter"],
    )
    def test_matrix(self, raw_empty, active_filters, expected):
        raw = pd.DataFrame() if raw_empty else pd.DataFrame({"X": [1]})
        assert infer_state(raw, active_filters=active_filters) == expected
