"""Structural tests for the reference SQL catalogue in :mod:`utils.queries`.

These assert the invariants the SQL page relies on -- that every statement is
reachable from the picker, that labels and variants are unique, and that every
builder still produces a runnable statement.

Most cases build SQL text only and need no connection. The Cortex Inference API
builder is the exception: it estimates credits from observed model rates, so it
queries Snowflake to bind those rates as literals. Those cases carry the
``snowflake`` marker, so the rest of the suite can be run offline with
``pytest -m "not snowflake"``.
"""

import datetime as dt

import pytest

from utils import queries as q

START = dt.date(2026, 7, 1)
END = dt.date(2026, 7, 31)

# Builders that must reach Snowflake to build their statement, by entry label.
NEEDS_CONNECTION = {q.SURFACE_REST_API}


def entry_params() -> list:
    """Parametrise over the catalogue, marking entries that need a connection."""
    return [
        pytest.param(
            entry,
            id=entry.label,
            marks=pytest.mark.snowflake if entry.label in NEEDS_CONNECTION else (),
        )
        for entry in q.SQL_REFERENCE
    ]


NAV_ORDER = [
    q.SURFACE_COCO,
    q.SURFACE_COWORK,
    q.SURFACE_AGENTS,
    q.SURFACE_SEARCH,
    q.SURFACE_REST_API,
    q.SURFACE_AI_FUNCTIONS,
    q.SURFACE_WAREHOUSE_ATTRIBUTION,
]


class TestGrouping:
    """The two-level picker must cover the catalogue exactly once."""

    def test_group_order_matches_the_top_nav(self):
        assert list(q.sql_reference_groups()) == NAV_ORDER

    def test_shared_is_not_a_page_level_group(self):
        assert q.SHARED not in q.sql_reference_groups()

    def test_every_entry_is_reachable_from_exactly_one_group(self):
        reachable = [
            entry
            for group in (*q.sql_reference_groups(), q.SHARED)
            for entry in q.sql_reference_for(group)
        ]
        assert len(reachable) == len(q.SQL_REFERENCE)
        assert {e.label for e in reachable} == {e.label for e in q.SQL_REFERENCE}

    def test_labels_are_unique(self):
        labels = [e.label for e in q.SQL_REFERENCE]
        assert len(set(labels)) == len(labels)

    @pytest.mark.parametrize("group", [*NAV_ORDER, q.SHARED])
    def test_variants_are_unique_within_a_group(self, group):
        variants = [e.variant for e in q.sql_reference_for(group)]
        assert len(set(variants)) == len(variants)

    @pytest.mark.parametrize("group", NAV_ORDER)
    def test_each_group_defaults_to_its_primary_fact(self, group):
        """The picker selects the first entry, so it must not be a fallback."""
        assert q.sql_reference_for(group)[0].kind == q.KIND_PRIMARY

    @pytest.mark.parametrize("group", NAV_ORDER)
    def test_each_group_has_exactly_one_primary_fact(self, group):
        primaries = [e for e in q.sql_reference_for(group) if e.kind == q.KIND_PRIMARY]
        assert len(primaries) == 1

    def test_shared_entries_are_all_labelled_as_shared_inputs(self):
        shared = q.sql_reference_for(q.SHARED)
        assert shared
        assert {e.kind for e in shared} == {q.KIND_SHARED_INPUT}

    def test_unknown_group_yields_nothing(self):
        assert q.sql_reference_for("Not A Group") == ()


class TestBuilders:
    """Every catalogued builder must still produce displayable SQL."""

    @pytest.mark.parametrize("entry", entry_params())
    def test_builds_non_empty_sql(self, entry, monkeypatch):
        monkeypatch.setattr(q, "utc_today", lambda: END)
        sql, _ = entry.builder(START, END)
        assert sql.strip()

    @pytest.mark.parametrize("entry", entry_params())
    def test_leaves_no_unbound_placeholder_when_inlined(self, entry, monkeypatch):
        """The page advertises paste-and-run SQL, so every ? must be substituted."""
        monkeypatch.setattr(q, "utc_today", lambda: END)
        sql, params = entry.builder(START, END)
        assert "?" not in q.inline_params(sql, params)

    @pytest.mark.parametrize("entry", q.SQL_REFERENCE, ids=[e.label for e in q.SQL_REFERENCE])
    def test_declares_at_least_one_source_view(self, entry):
        assert entry.sources
        assert all(s.startswith(q.AU) for s in entry.sources)


class TestInlineParams:
    """Display-only substitution used by the SQL page."""

    def test_substitutes_positionally(self):
        assert q.inline_params("SELECT ?, ?", ("a", "b")) == "SELECT 'a', 'b'"

    def test_leaves_sql_without_placeholders_alone(self):
        assert q.inline_params("SELECT 1", ()) == "SELECT 1"
