"""Plain data types for the selection engine. No database, no I/O, no clock, no randomness.

All numbers are `decimal.Decimal` so results are exact and reproducible by hand.
GB means GiB (2^30 bytes) everywhere, matching sql/schema.sql.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

GIB = Decimal(1073741824)

# The five scored metrics, in the order used everywhere (and the order of the DB weight columns).
METRICS = ("storage_cost", "transfer_cost", "latency", "reliability", "security")
MINIMISED = frozenset({"storage_cost", "transfer_cost", "latency"})  # lower raw value is better
SECURITY_LEVELS = ("LOW", "MEDIUM", "HIGH")
TIERS = ("HOT", "COOL", "COLD", "ARCHIVE")

METRIC_LABELS = {
    "storage_cost": "storage cost",
    "transfer_cost": "transfer and request cost",
    "latency": "latency",
    "reliability": "reliability",
    "security": "security",
}


@dataclass(frozen=True)
class TierPricing:
    """ILLUSTRATIVE unless the source row says otherwise (see Candidate.is_illustrative)."""

    tier_code: str
    storage_usd_per_gb_month: Decimal
    retrieval_usd_per_gb: Decimal
    put_usd_per_1k: Decimal
    get_usd_per_1k: Decimal
    min_storage_days: int = 0


@dataclass(frozen=True)
class Candidate:
    """One provider-region that could hold the file, with the data the engine needs."""

    region_id: int
    provider_id: int
    provider_code: str
    provider_name: str
    region_code: str
    provider_active: bool
    region_active: bool
    health_status: str  # UP, DEGRADED, DOWN
    reliability_score: Decimal  # 0..10
    security_score: Decimal  # 0..10
    max_object_bytes: int
    egress_usd_per_gb: Decimal
    pricing: TierPricing | None  # price row for the requested tier; None = not priced
    latency_ms: Decimal | None  # typical GET latency; None = no measurement
    is_illustrative: bool = True

    @property
    def label(self) -> str:
        return f"{self.provider_name} ({self.region_code})"


@dataclass(frozen=True)
class FileRequest:
    file_size_bytes: int
    security_level: str
    expected_accesses_per_month: Decimal = Decimal(1)
    retention_months: Decimal = Decimal(12)
    tier: str = "HOT"

    def validate(self) -> None:
        if self.file_size_bytes < 0:
            raise ValueError("file_size_bytes must be >= 0")
        if self.security_level not in SECURITY_LEVELS:
            raise ValueError(f"security_level must be one of {', '.join(SECURITY_LEVELS)}")
        if self.expected_accesses_per_month < 0:
            raise ValueError("expected_accesses_per_month must be >= 0")
        if self.retention_months <= 0:
            raise ValueError("retention_months must be > 0")
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {', '.join(TIERS)}")


@dataclass(frozen=True)
class Policy:
    """Minimum scores a provider-region must have for each file security level (hard filter)."""

    min_security: dict[str, Decimal]
    min_reliability: dict[str, Decimal]


@dataclass(frozen=True)
class Weights:
    storage_cost: Decimal
    transfer_cost: Decimal
    latency: Decimal
    reliability: Decimal
    security: Decimal

    def as_dict(self) -> dict[str, Decimal]:
        return {m: getattr(self, m) for m in METRICS}


@dataclass(frozen=True)
class CostBreakdown:
    size_gb: Decimal
    billed_months: Decimal
    reads: Decimal
    storage: Decimal
    request: Decimal
    retrieval: Decimal
    egress: Decimal

    @property
    def transfer(self) -> Decimal:
        return self.request + self.retrieval + self.egress

    @property
    def total(self) -> Decimal:
        return self.storage + self.transfer


@dataclass(frozen=True)
class Rejection:
    candidate: Candidate
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    candidate: Candidate
    cost: CostBreakdown
    raw: dict[str, Decimal]  # raw value of each metric
    normalised: dict[str, Decimal]  # min-max value, 0..1, before direction is applied
    score: dict[str, Decimal]  # 0..1, higher is always better
    contribution: dict[str, Decimal]  # weight * score (exact)
    composite: Decimal  # sum of contributions rounded to 6 decimals


@dataclass(frozen=True)
class SelectionResult:
    request: FileRequest
    weights: Weights
    weights_note: str | None
    ranked: tuple[RankedCandidate, ...]
    rejected: tuple[Rejection, ...]
    selected: RankedCandidate | None
    advantages: tuple[str, ...] = field(default_factory=tuple)
    disadvantages: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BaselineResult:
    name: str
    candidate: Candidate | None
    cost: CostBreakdown | None
    note: str
