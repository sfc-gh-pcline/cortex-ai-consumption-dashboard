"""Pandas aggregation over the fact frames returned by :mod:`utils.queries`.

Every page issues one aggregate query, then derives KPIs, breakdowns and charts
from the resulting frame here, so filters respond without re-querying.

Every function tolerates ``None`` and empty frames, because pages call them
before knowing whether the window has any usage in it.
"""

import datetime as dt
from collections.abc import Sequence

import pandas as pd

from utils import queries as q
from utils.view_state import (
    FILTER_CAPABILITIES,
    ComparisonPeriods,
    ComparisonResult,
    ComparisonState,
    CoverageState,
    DateWindow,
    FilterSelection,
    ScopedFacts,
    SourceCoverage,
)

METRICS = ["CREDITS", "TOKENS", "CALLS"]

UNTAGGED = "(untagged)"


def apply_filters(
    df: pd.DataFrame | None,
    users: Sequence[str] | None = None,
    models: Sequence[str] | None = None,
    tag_users: Sequence[str] | None = None,
    *,
    source: str | None = None,
    page_values: dict[str, Sequence[str]] | None = None,
) -> ScopedFacts:
    """Partition global and page selections without inventing attribution.

    Args:
        df: Fact frame to filter.
        users: Usernames to keep. Empty or None keeps every user.
        models: Model names to keep. Unsupported attribution is unscoped.
        tag_users: Usernames carrying the selected user tags, from
            :func:`users_for_tags`. Distinct from ``users``: an empty list here
            means the tag filter matched nobody with known user attribution,
            whereas None means no tag filter is active.
        source: Capability identity for dedicated frames, even with a SURFACE column.
        page_values: Selected values keyed by fact column.

    Returns:
        Disjoint matching, non-attributable, and excluded frames.
    """
    selection = FilterSelection(
        users=tuple(users or ()),
        models=tuple(models or ()),
        page_values={column: tuple(values) for column, values in (page_values or {}).items()},
    )
    return partition_scope(df, selection, source=source, tag_users=tag_users)


def partition_scope(
    frame: pd.DataFrame | None,
    selection: FilterSelection,
    *,
    source: str | None = None,
    tag_users: Sequence[str] | None = None,
) -> ScopedFacts:
    """Assign every row once, with known mismatches preceding unknown dimensions.

    Args:
        frame: Source facts, never modified in place.
        selection: Global selections and column-keyed local selections.
        source: Dedicated source identity; unified frames use their SURFACE values.
        tag_users: Resolved users, empty for no matches, None for inactive/unavailable.

    Returns:
        Disjoint frames preserving order, indices, and values. Missing tag lookup
        with active tags is unknown rather than silently disabling the filter.
    """
    if frame is None:
        frame = pd.DataFrame()
    unknown = pd.Series(False, index=frame.index)
    excluded = pd.Series(False, index=frame.index)
    predicates = [("USER_NAME", selection.users), ("MODEL_NAME", selection.models)]
    predicates.extend(selection.page_values.items())
    if tag_users is not None:
        predicates.append(("USER_NAME", tuple(tag_users)))
    elif selection.tags:
        unknown[:] = True

    for position, (column, values) in enumerate(predicates):
        is_tag_predicate = tag_users is not None and position == len(predicates) - 1
        if not values and not is_tag_predicate:
            continue
        if source is not None:
            supported = pd.Series(column in FILTER_CAPABILITIES.get(source, ()), index=frame.index)
        elif "SURFACE" in frame.columns:
            supported = frame["SURFACE"].map(
                lambda surface, column=column: column in FILTER_CAPABILITIES.get(surface, ())
            )
        else:
            supported = pd.Series(column in frame.columns, index=frame.index)
        if column not in frame.columns:
            unknown[:] = True
            continue
        known = supported & frame[column].notna() & ~frame[column].isin([q.UNKNOWN, ""])
        excluded |= known & ~frame[column].isin(values)
        unknown |= ~known
    return ScopedFacts(
        frame.loc[~excluded & ~unknown].copy(),
        frame.loc[~excluded & unknown].copy(),
        frame.loc[excluded].copy(),
    )


def options(df: pd.DataFrame | None, column: str) -> list[str]:
    """List the distinct values of a column, for a filter widget.

    Args:
        df: Frame to read.
        column: Column to collect values from.

    Returns:
        Sorted distinct values as strings, with nulls dropped. Empty if the
        frame or column is absent.
    """
    if df is None or df.empty or column not in df.columns:
        return []
    return sorted(df[column].dropna().astype(str).unique().tolist())


def totals(df: pd.DataFrame | None) -> dict[str, float]:
    """Sum the headline metrics of a fact frame into a KPI-ready mapping.

    Args:
        df: Fact frame carrying CREDITS, TOKENS, CALLS and USAGE_DATE.
            USER_NAME is optional; surfaces with no user dimension (Cortex
            Search) get USERS=0 rather than a KeyError.

    Returns:
        CREDITS, TOKENS, CALLS, USERS and ACTIVE_DAYS. All zero for an empty
        frame, so callers can index the result unconditionally.
    """
    if df is None or df.empty:
        return {"CREDITS": 0.0, "TOKENS": 0, "CALLS": 0, "USERS": 0, "ACTIVE_DAYS": 0}
    return {
        "CREDITS": float(df["CREDITS"].sum()),
        "TOKENS": int(df["TOKENS"].sum()),
        "CALLS": int(df["CALLS"].sum()),
        "USERS": int(df.loc[~df["USER_NAME"].isin([q.UNKNOWN, ""]), "USER_NAME"].nunique())
        if "USER_NAME" in df.columns
        else 0,
        "ACTIVE_DAYS": int(df["USAGE_DATE"].nunique()),
    }


def window_days(start: dt.date, end: dt.date) -> int:
    """Measure the inclusive length of the selected date window.

    Args:
        start: First day in the window.
        end: Last day in the window.

    Returns:
        The day count, floored at one. Pages divide totals by this for the
        per-day rates on the KPI cards, so it must never be zero.
    """
    return max((end - start).days + 1, 1)


def sum_column(df: pd.DataFrame | None, column: str) -> float:
    """Sum one column, tolerating a missing column or empty frame.

    Args:
        df: Frame to read.
        column: Column to sum.

    Returns:
        The total, or 0.0 if the frame or column is absent.
    """
    if df is None or df.empty or column not in df.columns:
        return 0.0
    return float(df[column].sum())


def by_dimension(
    df: pd.DataFrame | None,
    column: str,
    label_col: str = "DIMENSION",
    metrics: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Aggregate to one row per dimension value.

    Args:
        df: Fact frame carrying the metric columns to sum. Metrics absent from
            the frame are skipped rather than raising, because not every source
            view offers the full set: Cortex Search serving has no TOKENS, and
            the warehouse-attribution fact carries its own credit columns.
            USER_NAME is likewise optional -- surfaces with no user dimension
            simply get no USERS column back.
        column: Column to group by. Nulls are grouped and relabelled
            :data:`utils.queries.UNKNOWN`.
        label_col: Name to give the grouping column in the result, so the
            caller can keep a meaningful header.
        metrics: Metric columns to sum, in display and sort precedence order.
            Defaults to :data:`METRICS` (the Cortex token-credit triple). Pass
            an explicit list for frames metered on something else.

    Returns:
        One row per distinct value with whichever of ``metrics`` the frame
        actually has, plus -- if the frame has USER_NAME -- a distinct USERS
        count, sorted descending by the first metric present. Empty with the
        requested columns if the frame, the group column, or every metric is
        absent.
    """
    requested = list(metrics) if metrics is not None else list(METRICS)
    has_users = df is not None and "USER_NAME" in df.columns
    present = [m for m in requested if df is not None and m in df.columns]
    if df is None or df.empty or column not in df.columns or not present:
        columns = [label_col] + requested + (["USERS"] if has_users else [])
        return pd.DataFrame(columns=columns)

    aggs: dict[str, tuple[str, str]] = {m: (m, "sum") for m in present}
    if has_users:
        aggs["USERS"] = ("USER_NAME", "nunique")

    grouped = (
        df.groupby(column, dropna=False)
        .agg(**aggs)
        .reset_index()
        .rename(columns={column: label_col})
    )
    grouped[label_col] = grouped[label_col].fillna(q.UNKNOWN).astype(str)
    return grouped.sort_values(present[0], ascending=False, ignore_index=True)


def leader(
    frame: pd.DataFrame, total: float, value_col: str = "CREDITS"
) -> tuple[pd.Series, float] | None:
    """Pick the top row of a breakdown and its share of the total.

    Args:
        frame: Output of :func:`by_dimension`, already sorted descending by the
            metric of interest.
        total: Value to take the share against, usually the window total.
        value_col: Column holding the metric to take the share from. Defaults to
            CREDITS; pass the frame's own metric when it isn't metered in Cortex
            token credits.

    Returns:
        The leading row and its share of ``total`` as a fraction, or None if
        there is no row to lead or ``value_col`` is absent. The share is 0.0
        when ``total`` is zero, so callers never divide by it themselves.
    """
    if frame is None or frame.empty or value_col not in frame.columns:
        return None
    top = frame.iloc[0]
    return top, (top[value_col] / total if total else 0.0)


def daily(df: pd.DataFrame | None, color_col: str | None = None) -> pd.DataFrame:
    """Aggregate to daily totals, optionally split by a series column.

    Args:
        df: Fact frame carrying USAGE_DATE, CREDITS, TOKENS and CALLS.
        color_col: Column to split the series by, for a stacked or multi-line
            chart. Ignored if absent from the frame.

    Returns:
        One row per day (per series value, when splitting), sorted by date.
        Empty with USAGE_DATE and the metric columns for an empty frame.
    """
    if df is None:
        return pd.DataFrame(columns=pd.Index(["USAGE_DATE", *METRICS]))
    window = df.attrs.get("display_window")
    if window is not None:
        coverages = df.attrs.get("source_coverages", ())
        groups = [(None, df)]
        if color_col and color_col in df:
            labels = df.attrs.get("series_values", {}).get(
                color_col, tuple(df[color_col].dropna().unique())
            )
            groups = [(label, df.loc[df[color_col].eq(label)]) for label in labels]
            if not groups:
                groups = [(None, df)]
        frames = []
        for label, subset in groups:
            evidence = coverages
            if color_col == "SURFACE" and label is not None:
                evidence = tuple(
                    item for item in coverages if item.dependency == q.main_dependency_for(label)
                )
            completed = calendar_daily(subset, window, evidence)
            for metric in ("TOKENS", "CALLS"):
                completed[metric] = calendar_daily(subset, window, evidence, metric)[metric]
            if color_col and color_col in df and label is not None:
                completed[color_col] = label
            frames.append(completed)
        result = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=pd.Index(["USAGE_DATE", *METRICS]))
        )
        if previous := df.attrs.get("previous_daily"):
            result.attrs["previous_daily"] = previous
        return result
    if df.empty:
        return pd.DataFrame(columns=pd.Index(["USAGE_DATE", *METRICS]))

    keys = ["USAGE_DATE"] + ([color_col] if color_col and color_col in df.columns else [])
    grouped = (
        df.groupby(keys, dropna=False)
        .agg(CREDITS=("CREDITS", "sum"), TOKENS=("TOKENS", "sum"), CALLS=("CALLS", "sum"))
        .reset_index()
    )
    return grouped.sort_values("USAGE_DATE", ignore_index=True)


def relative_period_daily(
    current: pd.DataFrame, previous: pd.DataFrame, metric: str = "CREDITS"
) -> pd.DataFrame:
    """Align two daily calendars by day number without inventing missing observations."""
    frames = []
    for label, source in (("Current", current), ("Previous", previous)):
        values = source[["USAGE_DATE", metric]].copy()
        values.attrs = {}
        values["USAGE_DATE"] = pd.to_datetime(values["USAGE_DATE"])
        values = values.groupby("USAGE_DATE", as_index=False)[metric].agg(
            lambda group: group.sum(min_count=len(group))
        )
        values["Day"] = range(1, len(values) + 1)
        values["Period"] = label
        frames.append(values)
    if len(frames[0]) != len(frames[1]):
        raise ValueError("Comparison calendars must contain equal numbers of days.")
    return pd.concat(frames, ignore_index=True)


def daily_trend(df: pd.DataFrame | None, value_col: str = "CREDITS") -> list[float] | None:
    """Build the sparkline series for ``st.metric``.

    Args:
        df: Fact frame to summarise by day.
        value_col: Metric to plot.

    Returns:
        One value per calendar day, or None if an unknown gap cannot be rendered safely.
    """
    if df is None:
        return None
    window = df.attrs.get("display_window")
    coverages = df.attrs.get("source_coverages", ())
    if window is None:
        return None
    frame = calendar_daily(df, window, coverages, value_col)
    if frame[value_col].isna().any():
        return None
    return frame[value_col].astype(float).tolist()


def trend_delta(df: pd.DataFrame | None, value_col: str = "CREDITS") -> str | None:
    """Format only an explicitly evaluated equal-period comparison.

    Args:
        df: Current-period facts annotated by the shared scope boundary.
        value_col: Evaluated metric name.

    Returns:
        Percentage plus absolute change, or None when coverage is not comparable.
    """
    if df is None:
        return None
    result = df.attrs.get("comparisons", {}).get(value_col)
    if result is None or result.delta_percent is None:
        return None
    absolute = result.current - result.previous
    return f"{result.delta_percent:+.1f}% ({absolute:+.4f})"


def period_slice(frame: pd.DataFrame, window: DateWindow | None) -> pd.DataFrame:
    """Return only facts inside the inclusive UTC interval, preserving metadata."""
    if window is None or frame.empty or "USAGE_DATE" not in frame:
        return frame.iloc[:0].copy()
    dates = pd.to_datetime(frame["USAGE_DATE"], utc=True).dt.date
    return frame.loc[dates.between(window.start, window.end)].copy()


def covers_window(coverage: SourceCoverage, window: DateWindow) -> bool:
    """Require verified read bounds, retention, and completeness for every day."""
    return (
        coverage.state in {CoverageState.AVAILABLE, CoverageState.PARTIAL}
        and coverage.actual_source == "direct"
        and coverage.served is not None
        and coverage.retention_start is not None
        and coverage.complete_through is not None
        and coverage.served.start <= window.start
        and window.end <= coverage.served.end
        and coverage.retention_start <= window.start
        and window.end <= coverage.complete_through
    )


def compare_periods(
    frame: pd.DataFrame | None,
    periods: ComparisonPeriods,
    coverages: Sequence[SourceCoverage],
    value_col: str = "CREDITS",
) -> ComparisonResult:
    """Compare adjacent completed periods after the same scope predicate.

    Args:
        frame: Matching or explicitly unscoped union facts.
        periods: Current/prior calendar intervals.
        coverages: Every included dependency, including successful empty sources.
        value_col: Additive metric to compare.

    Returns:
        Observed values and a percentage only when every dependency covers both periods.
    """
    if frame is None:
        return ComparisonResult(
            None, None, None, ComparisonState.UNAVAILABLE, "A required source is unavailable."
        )
    if periods.current is None or periods.previous is None:
        return ComparisonResult(
            None, None, None, ComparisonState.INCOMPLETE, "No completed comparison period."
        )
    if (
        periods.current.days != periods.previous.days
        or periods.previous.end + dt.timedelta(days=1) != periods.current.start
    ):
        raise ValueError("Comparison periods must be adjacent and equal length.")
    current = period_slice(frame, periods.current)
    previous = period_slice(frame, periods.previous)
    current_value = sum_column(current, value_col)
    previous_value = sum_column(previous, value_col)
    if any(item.state == CoverageState.UNAVAILABLE for item in coverages):
        return ComparisonResult(
            current_value,
            previous_value,
            None,
            ComparisonState.UNAVAILABLE,
            "A required source is unavailable; observed values exclude it.",
        )
    reason = None
    if value_col not in frame.columns:
        reason = "The metric is unavailable."
    elif frame[value_col].isna().any():
        reason = "Some metric values are unavailable."
    elif value_col == "CREDITS" and "IS_UNRATED" in frame and frame["IS_UNRATED"].eq(1).any():
        reason = "Credit change is unavailable because one or both periods contain inference usage without an observed model rate."
    elif value_col == "CREDITS" and "RATE_INCOMPLETE" in frame and frame["RATE_INCOMPLETE"].any():
        reason = "Credit change is unavailable because one or both periods lack pricing details for some inference tokens (model rates, token types, or their breakdown)."
    elif not coverages or not all(
        covers_window(item, window)
        for item in coverages
        for window in (periods.current, periods.previous)
    ):
        reason = "Source coverage does not verify both complete periods."
    if reason:
        return ComparisonResult(
            current_value, previous_value, None, ComparisonState.INCOMPLETE, reason
        )
    if previous_value == 0:
        both_zero = current_value == 0
        return ComparisonResult(
            current_value,
            previous_value,
            0.0 if both_zero else None,
            ComparisonState.BOTH_ZERO if both_zero else ComparisonState.ZERO_BASELINE,
            "No change" if both_zero else "New usage",
        )
    return ComparisonResult(
        current_value,
        previous_value,
        (current_value - previous_value) / previous_value * 100,
        ComparisonState.COMPARABLE,
    )


def calendar_daily(
    frame: pd.DataFrame,
    window: DateWindow,
    coverages: Sequence[SourceCoverage],
    value_col: str = "CREDITS",
) -> pd.DataFrame:
    """Complete the calendar, filling absent values only on verified covered days."""
    selected = period_slice(frame, window)
    if not selected.empty and value_col in selected:
        selected = selected.copy()
        selected["USAGE_DATE"] = pd.to_datetime(selected["USAGE_DATE"], utc=True).dt.date
        observed = selected.groupby("USAGE_DATE")[value_col].sum(min_count=1)
    else:
        observed = pd.Series(dtype=float)
    rows = []
    for timestamp in pd.date_range(window.start, window.end):
        day = timestamp.date()
        covered = bool(coverages) and all(
            covers_window(item, DateWindow(day, day)) for item in coverages
        )
        rows.append(
            {"USAGE_DATE": day, value_col: observed.get(day, 0.0 if covered else float("nan"))}
        )
    return pd.DataFrame(rows)


def token_mix(df: pd.DataFrame | None) -> pd.DataFrame:
    """Aggregate per-token-type totals into long form for charting.

    Args:
        df: Fact frame carrying the :data:`utils.queries.TOKEN_TYPE_COLUMNS`.
            Token-type columns the frame does not have are skipped.

    Returns:
        One row per token type with a display DIMENSION label and TOKENS total,
        sorted descending. Types totalling zero are dropped, so a surface that
        never caches shows no cache slices.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=pd.Index(["DIMENSION", "TOKENS"]))

    rows = []
    for token_type, column in q.TOKEN_TYPE_COLUMNS.items():
        if column in df.columns:
            rows.append(
                {
                    "DIMENSION": q.TOKEN_TYPE_LABELS[token_type],
                    "TOKENS": float(df[column].sum()),
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame[frame["TOKENS"] > 0].sort_values("TOKENS", ascending=False, ignore_index=True)


# ---------------------------------------------------------------------------
# User tags
# ---------------------------------------------------------------------------


def tag_label_options(tags: pd.DataFrame | None) -> list[str]:
    """List the ``name = value`` tag labels available for filtering.

    Args:
        tags: Tag reference frame from :func:`utils.queries.load_user_tags`.

    Returns:
        Sorted distinct tag labels, empty if there are no tags.
    """
    if tags is None or tags.empty:
        return []
    return sorted(tags["TAG_LABEL"].dropna().astype(str).unique().tolist())


def users_for_tags(tags: pd.DataFrame | None, selected_labels: Sequence[str] | None) -> list[str]:
    """Resolve tag labels to the usernames carrying them.

    Args:
        tags: Tag reference frame from :func:`utils.queries.load_user_tags`.
        selected_labels: Tag labels to match. Empty or None matches nobody.

    Returns:
        Sorted distinct usernames carrying any of the labels. Feed this to the
        ``tag_users`` argument of :func:`apply_filters`.
    """
    if tags is None or tags.empty or not selected_labels:
        return []
    matched = tags[tags["TAG_LABEL"].isin(selected_labels)]
    return sorted(matched["USER_NAME"].dropna().astype(str).unique().tolist())


def by_user_tag(df: pd.DataFrame | None, tags: pd.DataFrame | None) -> pd.DataFrame:
    """Group credits and tokens by the tags on the user who spent them.

    Args:
        df: Fact frame carrying USER_NAME.
        tags: Tag reference frame from :func:`utils.queries.load_user_tags`.

    Returns:
        One row per tag label, shaped like :func:`by_dimension`. Users with
        several tags count once per tag, so shares are per-tag rather than a
        partition of the total. Users with no tag land under
        :data:`UNTAGGED`.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=pd.Index(["TAG_LABEL", *METRICS, "USERS"]))

    if tags is None or tags.empty:
        lookup = pd.DataFrame(columns=pd.Index(["USER_NAME", "TAG_LABEL"]))
    else:
        lookup = tags[["USER_NAME", "TAG_LABEL"]].drop_duplicates()

    merged = df.merge(lookup, on="USER_NAME", how="left")
    merged["TAG_LABEL"] = merged["TAG_LABEL"].fillna(UNTAGGED)
    return by_dimension(merged, "TAG_LABEL", label_col="TAG_LABEL")


def by_agent_tag(df: pd.DataFrame | None) -> pd.DataFrame:
    """Explode the pipe-joined agent tag labels into one row per tag.

    Args:
        df: Fact frame carrying AGENT_TAG_LABELS, as built by
            :func:`utils.queries.agent_facts_sql`.

    Returns:
        One row per tag label, shaped like :func:`by_dimension`. Agents with
        several tags count once per tag; untagged agents land under
        :data:`UNTAGGED`.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=pd.Index(["TAG_LABEL", *METRICS, "USERS"]))

    frame = df.copy()
    frame["TAG_LABEL"] = frame["AGENT_TAG_LABELS"].fillna("").replace("", UNTAGGED).str.split("|")
    frame = frame.explode("TAG_LABEL")
    frame["TAG_LABEL"] = frame["TAG_LABEL"].replace("", UNTAGGED).fillna(UNTAGGED)
    return by_dimension(frame, "TAG_LABEL", label_col="TAG_LABEL")


# ---------------------------------------------------------------------------
# Cross-surface warehouse attribution
#
# warehouse_credit_facts (utils.queries.load_warehouse_credit_facts) already
# carries SURFACE, TIER and REQUEST_ID from the 1:1 SQL-side join (Task 1.1),
# so filtering to one surface and splitting by tier here is a pandas
# operation, not a second query -- the per-surface KPI card and the
# Attribution page both read the one shared fact.
# ---------------------------------------------------------------------------


def warehouse_credits_for_surface(wh_facts: pd.DataFrame | None, surface: str) -> dict[str, float]:
    """Split one surface's warehouse credits into per-request and surface-only.

    Args:
        wh_facts: The frame from :func:`utils.queries.WarehouseFactsResult.frame`.
        surface: A `utils.queries.SURFACE_*` value to filter to.

    Returns:
        ``{"PER_REQUEST": ..., "SURFACE_ONLY": ..., "TOTAL": ...}``. All zero
        if the frame is empty or the surface has no rows. ``PER_REQUEST`` is
        a tier classified by request/query identity, not user/model filtering.
        Both tiers are scoped only to the provided dates and service.
    """
    if wh_facts is None or wh_facts.empty:
        return {"PER_REQUEST": 0.0, "SURFACE_ONLY": 0.0, "TOTAL": 0.0}

    subset = wh_facts[wh_facts["SURFACE"] == surface]
    if subset.empty:
        return {"PER_REQUEST": 0.0, "SURFACE_ONLY": 0.0, "TOTAL": 0.0}

    per_request = float(subset.loc[subset["TIER"] == "per_request", "WAREHOUSE_CREDITS"].sum())
    surface_only = float(subset.loc[subset["TIER"] == "surface", "WAREHOUSE_CREDITS"].sum())
    return {
        "PER_REQUEST": per_request,
        "SURFACE_ONLY": surface_only,
        "TOTAL": per_request + surface_only,
    }
