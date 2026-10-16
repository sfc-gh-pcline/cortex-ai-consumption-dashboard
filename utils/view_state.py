"""Shared value contracts for scope, periods, coverage, and stable navigation."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

import pandas as pd


class CoverageState(StrEnum):
    """Distinguish source availability from measured consumption."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ComparisonState(StrEnum):
    """Explain whether a percentage comparison is meaningful."""

    COMPARABLE = "comparable"
    ZERO_BASELINE = "zero_baseline"
    BOTH_ZERO = "both_zero"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DateWindow:
    """Inclusive UTC calendar interval."""

    start: date
    end: date

    def __post_init__(self) -> None:
        """Reject reversed windows before any source read."""
        if self.end < self.start:
            raise ValueError("Window end must be on or after its start.")

    @property
    def days(self) -> int:
        """Return the inclusive calendar length."""
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class ComparisonPeriods:
    """Requested range with separate completed and partial-day intervals."""

    requested: DateWindow
    current: DateWindow | None
    previous: DateWindow | None
    partial_today: DateWindow | None = None


@dataclass(frozen=True)
class FilterSelection:
    """Selections retained across navigation, including page-specific columns."""

    users: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    page_values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Own immutable copies rather than retaining mutable caller inputs."""
        for name in ("users", "models", "tags"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(
            self,
            "page_values",
            MappingProxyType({name: tuple(values) for name, values in self.page_values.items()}),
        )

    @property
    def active(self) -> bool:
        """Return whether any dimension selection narrows this view."""
        return bool(self.users or self.models or self.tags or any(self.page_values.values()))


@dataclass(frozen=True)
class SourceCoverage:
    """Metadata produced by the same read that produced a dependency frame."""

    dependency: str
    actual_source: str
    fetched_at: datetime | None
    requested: DateWindow
    served: DateWindow | None
    retention_start: date | None = None
    latency_hours: float | None = None
    snapshot_at: datetime | None = None
    state: CoverageState = CoverageState.UNKNOWN
    reason: str | None = None
    read_started_at: datetime | None = None
    complete_through: date | None = None
    # Newest USAGE_DATE present in the returned rows; None when the read had no rows
    # or no date column. Lets the UI say "no usage recorded after <day>" so a gap is
    # attributed to the source view rather than to the dashboard.
    latest_usage: date | None = None


@dataclass(frozen=True)
class LoadedDependency:
    """Frame and serializable source-read evidence, cached together."""

    frame: pd.DataFrame
    coverage: SourceCoverage


@dataclass(frozen=True)
class ScopedFacts:
    """Disjoint partitions retaining each input row and its original measures."""

    matched: pd.DataFrame
    unscoped: pd.DataFrame
    excluded: pd.DataFrame


@dataclass(frozen=True)
class ComparisonResult:
    """Measured values plus an explicit comparison outcome."""

    current: float | None
    previous: float | None
    delta_percent: float | None
    state: ComparisonState
    reason: str | None = None


@dataclass(frozen=True)
class PageIdentity:
    """Stable route and dependency identity independent of a display title."""

    page_id: str
    title: str
    dependency_key: str
    group: str

    @property
    def path(self) -> str:
        """Return the preserved page entrypoint."""
        return f"app_pages/{self.page_id}.py"

    @property
    def url_path(self) -> str:
        """Keep Overview at the root and all other existing routes unchanged."""
        return "" if self.page_id == "total_consumption" else self.page_id


PAGES = (
    PageIdentity("total_consumption", "Overview", "Total Consumption", ""),
    PageIdentity("coco", "CoCo", "CoCo", "AI Services"),
    PageIdentity("cowork", "CoWork", "CoWork", "AI Services"),
    PageIdentity("cortex_agents", "Cortex Agents", "Cortex Agents", "AI Services"),
    PageIdentity("cortex_search", "Cortex Search", "Cortex Search", "AI Services"),
    PageIdentity(
        "cortex_inference_api", "Cortex Inference API", "Cortex Inference API", "AI Services"
    ),
    PageIdentity(
        "cortex_ai_functions", "Cortex AI Functions", "Cortex AI Functions", "AI Services"
    ),
    PageIdentity(
        "warehouse_attribution", "Warehouse Attribution", "Warehouse Attribution", "Infrastructure"
    ),
    PageIdentity("sql", "Reference SQL", "Reference SQL", "Reference"),
    PageIdentity(
        "acceleration_sql", "Query Acceleration SQL", "Query Acceleration SQL", "Reference"
    ),
    PageIdentity("config", "Acceleration Settings", "Config", "Administration"),
)

FILTER_CAPABILITIES = MappingProxyType(
    {
        "CoCo": frozenset({"USER_NAME", "MODEL_NAME", "SUB_SURFACE", "ROLE_NAME"}),
        "CoWork": frozenset({"USER_NAME", "COWORK_NAME", "AGENT_FQN"}),
        "CoWork service mix": frozenset({"USER_NAME"}),
        "Cortex Agents": frozenset({"USER_NAME", "AGENT_FQN"}),
        "Cortex Search": frozenset({"SERVICE_NAME", "CONSUMPTION_TYPE"}),
        "Cortex Inference API": frozenset(
            {"USER_NAME", "MODEL_NAME", "INFERENCE_REGION", "QUERY_TAG"}
        ),
        "Cortex AI Functions": frozenset({"USER_NAME", "MODEL_NAME", "FUNCTION_NAME", "QUERY_TAG"}),
        "Warehouse Attribution": frozenset({"SURFACE", "WAREHOUSE_NAME", "TIER"}),
    }
)
