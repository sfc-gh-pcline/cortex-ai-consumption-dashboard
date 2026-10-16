"""Aggregation tests for :mod:`utils.agg`.

These run offline: every case builds its own frame, so no Snowflake connection or
warehouse is needed and the suite is safe to run in CI.
"""

import pandas as pd
import pytest

from utils import agg


def make_facts() -> pd.DataFrame:
    """Build a fact frame with the full Cortex metric triple and a user column."""
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


class TestApplyFilters:
    """Cover global filters across both unified and surface-specific fact frames."""

    def test_model_filter_returns_empty_for_a_missing_page_model(self):
        """A globally selected model can be absent from the current surface."""
        frame = pd.DataFrame(
            {
                "USER_NAME": ["ALICE"],
                "MODEL_NAME": ["claude-opus-4-6"],
                "CREDITS": [1.0],
            }
        )

        filtered = agg.apply_filters(frame, models=["claude-haiku-4-5"])

        assert filtered.matched.empty
        assert filtered.excluded["CREDITS"].sum() == 1.0
        assert filtered.unscoped.empty


class TestByDimension:
    """Cover the metric-selection behaviour that two page crashes came from."""

    def test_sums_per_dimension_value(self):
        out = agg.by_dimension(make_facts(), "SURFACE", label_col="SURFACE")
        assert list(out["SURFACE"]) == ["CoCo", "CoWork"]
        assert out.loc[out["SURFACE"] == "CoCo", "CREDITS"].item() == 4.0
        assert out.loc[out["SURFACE"] == "CoCo", "CALLS"].item() == 3

    def test_sorted_by_first_metric_descending(self):
        out = agg.by_dimension(make_facts(), "SURFACE", label_col="SURFACE")
        assert out["CREDITS"].is_monotonic_decreasing

    def test_counts_distinct_users_when_present(self):
        out = agg.by_dimension(make_facts(), "SURFACE", label_col="SURFACE")
        assert out.loc[out["SURFACE"] == "CoCo", "USERS"].item() == 1

    def test_omits_users_when_frame_has_no_user_column(self):
        frame = make_facts().drop(columns=["USER_NAME"])
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE")
        assert "USERS" not in out.columns

    def test_skips_metrics_the_frame_lacks(self):
        """Cortex Search serving reports credits and calls but no tokens."""
        frame = make_facts().drop(columns=["TOKENS"])
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE")
        assert "TOKENS" not in out.columns
        assert {"CREDITS", "CALLS"} <= set(out.columns)

    def test_honours_an_explicit_metric_list(self):
        """The warehouse fact is metered in its own credit columns."""
        frame = pd.DataFrame(
            {
                "SURFACE": ["CoCo", "CoCo", "CoWork"],
                "WAREHOUSE_CREDITS": [1.5, 2.5, 0.5],
                "COMPUTE_CREDITS": [1.5, 2.5, 0.5],
            }
        )
        metrics = ["WAREHOUSE_CREDITS", "COMPUTE_CREDITS"]
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE", metrics=metrics)
        assert list(out.columns) == ["SURFACE", *metrics]
        assert out.loc[out["SURFACE"] == "CoCo", "WAREHOUSE_CREDITS"].item() == 4.0

    def test_preserves_the_total(self):
        frame = make_facts()
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE")
        assert out["CREDITS"].sum() == pytest.approx(frame["CREDITS"].sum())

    def test_relabels_nulls_as_unknown(self):
        frame = make_facts()
        frame.loc[0, "SURFACE"] = None
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE")
        assert "(unknown)" in {v.lower() for v in out["SURFACE"]}

    @pytest.mark.parametrize(
        "frame",
        [None, pd.DataFrame(), pd.DataFrame({"OTHER": [1]})],
        ids=["none", "empty", "missing-group-column"],
    )
    def test_returns_requested_columns_without_raising(self, frame):
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE")
        assert out.empty
        assert list(out.columns) == ["SURFACE", *agg.METRICS]

    def test_returns_empty_when_no_metric_is_present(self):
        frame = pd.DataFrame({"SURFACE": ["CoCo"], "SOMETHING_ELSE": [1]})
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE")
        assert out.empty

    @pytest.mark.parametrize("label_col", ["DIMENSION", "SURFACE", "SERVICE_NAME"])
    def test_puts_the_label_column_first(self, label_col):
        """``ui.breakdown_table`` reads column 0 when no label column is named."""
        populated = agg.by_dimension(make_facts(), "SURFACE", label_col=label_col)
        empty = agg.by_dimension(None, "SURFACE", label_col=label_col)
        assert populated.columns[0] == label_col
        assert empty.columns[0] == label_col


class TestLeader:
    """Cover the share-of-total helper, including non-credit metrics."""

    def test_returns_top_row_and_share(self):
        out = agg.by_dimension(make_facts(), "SURFACE", label_col="SURFACE")
        leader = agg.leader(out, 6.0)
        assert leader is not None
        top, share = leader
        assert top["SURFACE"] == "CoCo"
        assert share == pytest.approx(4.0 / 6.0)

    def test_share_is_zero_rather_than_dividing_by_zero(self):
        out = agg.by_dimension(make_facts(), "SURFACE", label_col="SURFACE")
        leader = agg.leader(out, 0.0)
        assert leader is not None
        _, share = leader
        assert share == 0.0

    def test_reads_an_alternate_value_column(self):
        frame = pd.DataFrame({"SURFACE": ["CoCo"], "WAREHOUSE_CREDITS": [2.0]})
        out = agg.by_dimension(frame, "SURFACE", label_col="SURFACE", metrics=["WAREHOUSE_CREDITS"])
        leader = agg.leader(out, 4.0, value_col="WAREHOUSE_CREDITS")
        assert leader is not None
        top, share = leader
        assert top["SURFACE"] == "CoCo"
        assert share == pytest.approx(0.5)

    def test_returns_none_when_there_is_nothing_to_lead(self):
        assert agg.leader(pd.DataFrame(), 1.0) is None

    def test_returns_none_when_the_value_column_is_absent(self):
        out = agg.by_dimension(make_facts(), "SURFACE", label_col="SURFACE")
        assert agg.leader(out, 1.0, value_col="NOT_A_COLUMN") is None


class TestTotals:
    """Cover the KPI mapping pages index into unconditionally."""

    def test_sums_every_headline_metric(self):
        out = agg.totals(make_facts())
        assert out["CREDITS"] == pytest.approx(6.0)
        assert out["TOKENS"] == 60
        assert out["CALLS"] == 4
        assert out["USERS"] == 2
        assert out["ACTIVE_DAYS"] == 2

    def test_reports_no_users_when_the_column_is_absent(self):
        out = agg.totals(make_facts().drop(columns=["USER_NAME"]))
        assert out["USERS"] == 0

    @pytest.mark.parametrize("frame", [None, pd.DataFrame()], ids=["none", "empty"])
    def test_is_all_zero_for_an_empty_frame(self, frame):
        assert all(value == 0 for value in agg.totals(frame).values())


class TestSumColumn:
    """Cover the guarded single-column sum."""

    def test_sums_the_column(self):
        assert agg.sum_column(make_facts(), "CREDITS") == pytest.approx(6.0)

    @pytest.mark.parametrize(
        ("frame", "column"),
        [(None, "CREDITS"), (pd.DataFrame(), "CREDITS"), (make_facts(), "NOPE")],
        ids=["none", "empty", "missing-column"],
    )
    def test_is_zero_rather_than_raising(self, frame, column):
        assert agg.sum_column(frame, column) == 0.0
