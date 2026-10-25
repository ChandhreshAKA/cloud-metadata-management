"""SQL for the provider registry and the selection engine's read model.

Table and column names are those of sql/schema.sql (Chunk 1). Every statement is a module-level
constant using bind variables; updates write every column (the service does read-modify-write),
so no SQL is ever assembled from request data.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Any

from app.providers.models import PricingRow, ProviderRow, RegionRow
from app.selection.models import Candidate, Policy, TierPricing, Weights
from app.selection.presets import DEFAULT_POLICY

LATENCY_SAMPLES = 20  # latest successful GET probes per region used for the latency metric

_PROVIDER_COLUMNS = "provider_id, provider_code, provider_name, max_object_bytes, is_active"
_LIST_PROVIDERS = f"SELECT {_PROVIDER_COLUMNS} FROM cloud_providers ORDER BY provider_id"  # noqa: S608
_GET_PROVIDER = f"SELECT {_PROVIDER_COLUMNS} FROM cloud_providers WHERE provider_id = :provider_id"  # noqa: S608
_INSERT_PROVIDER = """
    INSERT INTO cloud_providers (provider_code, provider_name, max_object_bytes, is_active)
    VALUES (:code, :name, :max_object_bytes, 'Y')
    RETURNING provider_id INTO :new_id
"""
_UPDATE_PROVIDER = """
    UPDATE cloud_providers
       SET provider_name = :name, max_object_bytes = :max_object_bytes, is_active = :active
     WHERE provider_id = :provider_id
"""

_REGION_COLUMNS = (
    "region_id, provider_id, region_code, bucket_name, reliability_score, security_score, "
    "egress_usd_per_gb, health_status, status_changed_at, is_active, is_illustrative"
)
_LIST_REGIONS = f"SELECT {_REGION_COLUMNS} FROM provider_regions ORDER BY region_id"  # noqa: S608
_LIST_REGIONS_FOR_PROVIDER = (
    f"SELECT {_REGION_COLUMNS} FROM provider_regions "  # noqa: S608
    "WHERE provider_id = :provider_id ORDER BY region_id"
)
_GET_REGION = f"SELECT {_REGION_COLUMNS} FROM provider_regions WHERE region_id = :region_id"  # noqa: S608
_INSERT_REGION = """
    INSERT INTO provider_regions
        (provider_id, region_code, bucket_name, reliability_score, security_score,
         egress_usd_per_gb, is_illustrative)
    VALUES
        (:provider_id, :region_code, :bucket_name, :reliability, :security, :egress, :illustrative)
    RETURNING region_id INTO :new_id
"""
_UPDATE_REGION = """
    UPDATE provider_regions
       SET bucket_name = :bucket_name,
           reliability_score = :reliability,
           security_score = :security,
           egress_usd_per_gb = :egress,
           status_changed_at = CASE WHEN health_status <> :health
                                    THEN SYSTIMESTAMP ELSE status_changed_at END,
           health_status = :health,
           is_active = :active
     WHERE region_id = :region_id
"""

_PRICING_COLUMNS = (
    "region_id, tier_code, native_class_name, storage_usd_per_gb_month, retrieval_usd_per_gb, "
    "put_usd_per_1k, get_usd_per_1k, min_storage_days, is_illustrative"
)
_LIST_PRICING = f"SELECT {_PRICING_COLUMNS} FROM provider_pricing ORDER BY region_id, tier_code"  # noqa: S608
_LIST_PRICING_FOR_REGION = (
    f"SELECT {_PRICING_COLUMNS} FROM provider_pricing "  # noqa: S608
    "WHERE region_id = :region_id ORDER BY tier_code"
)
_UPSERT_PRICING = """
    MERGE INTO provider_pricing t
    USING (SELECT :region_id AS region_id, :tier_code AS tier_code FROM dual) s
       ON (t.region_id = s.region_id AND t.tier_code = s.tier_code)
    WHEN MATCHED THEN UPDATE SET
         native_class_name = :native_class_name,
         storage_usd_per_gb_month = :storage,
         retrieval_usd_per_gb = :retrieval,
         put_usd_per_1k = :put_1k,
         get_usd_per_1k = :get_1k,
         min_storage_days = :min_days,
         is_illustrative = :illustrative
    WHEN NOT MATCHED THEN INSERT
         (region_id, tier_code, native_class_name, storage_usd_per_gb_month, retrieval_usd_per_gb,
          put_usd_per_1k, get_usd_per_1k, min_storage_days, is_illustrative)
         VALUES
         (:region_id, :tier_code, :native_class_name, :storage, :retrieval,
          :put_1k, :get_1k, :min_days, :illustrative)
"""

_CANDIDATES = """
    SELECT r.region_id, r.provider_id, p.provider_code, p.provider_name, r.region_code,
           p.is_active, r.is_active, r.health_status, r.reliability_score, r.security_score,
           p.max_object_bytes, r.egress_usd_per_gb, r.is_illustrative,
           pr.tier_code, pr.storage_usd_per_gb_month, pr.retrieval_usd_per_gb,
           pr.put_usd_per_1k, pr.get_usd_per_1k, pr.min_storage_days
      FROM provider_regions r
      JOIN cloud_providers p ON p.provider_id = r.provider_id
      LEFT JOIN provider_pricing pr ON pr.region_id = r.region_id AND pr.tier_code = :tier
     ORDER BY r.provider_id, r.region_id
"""
_LATENCY_SAMPLES = """
    SELECT region_id, latency_ms
      FROM (SELECT region_id, latency_ms,
                   ROW_NUMBER() OVER (PARTITION BY region_id
                                      ORDER BY measured_at DESC, measurement_id DESC) AS rn
              FROM performance_measurements
             WHERE operation = 'GET' AND success = 'Y')
     WHERE rn <= :samples
"""
_WEIGHT_PROFILES = """
    SELECT profile_name, w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security,
           is_default
      FROM weight_profiles ORDER BY profile_name
"""
_POLICY_ROWS = """
    SELECT config_key, config_value FROM system_config
     WHERE config_key LIKE 'security.min_score.%' OR config_key LIKE 'selection.min_reliability.%'
"""

_SECURITY_PREFIX = "security.min_score."
_RELIABILITY_PREFIX = "selection.min_reliability."


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _yes(flag: Any) -> bool:
    return flag == "Y"


def _flag(value: bool) -> str:
    return "Y" if value else "N"


def _provider(row: tuple[Any, ...]) -> ProviderRow:
    return ProviderRow(int(row[0]), row[1], row[2], int(row[3]), _yes(row[4]))


def _region(row: tuple[Any, ...]) -> RegionRow:
    return RegionRow(
        int(row[0]), int(row[1]), row[2], row[3], _dec(row[4]), _dec(row[5]), _dec(row[6]),
        row[7], row[8], _yes(row[9]), _yes(row[10]),
    )  # fmt: skip


def _pricing(row: tuple[Any, ...]) -> PricingRow:
    return PricingRow(
        int(row[0]), row[1], row[2], _dec(row[3]), _dec(row[4]), _dec(row[5]), _dec(row[6]),
        int(row[7]), _yes(row[8]),
    )  # fmt: skip


class OracleProviderRepository:
    def __init__(self, connection: Any):
        self._conn = connection

    def _all(self, sql: str, binds: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        cursor = self._conn.cursor()
        cursor.execute(sql, binds or {})
        return cursor.fetchall()

    # --- providers ---------------------------------------------------------------------------
    def list_providers(self) -> list[ProviderRow]:
        return [_provider(r) for r in self._all(_LIST_PROVIDERS)]

    def get_provider(self, provider_id: int) -> ProviderRow | None:
        rows = self._all(_GET_PROVIDER, {"provider_id": provider_id})
        return _provider(rows[0]) if rows else None

    def create_provider(self, code: str, name: str, max_object_bytes: int) -> int:
        cursor = self._conn.cursor()
        new_id = cursor.var(int)
        cursor.execute(
            _INSERT_PROVIDER,
            {"code": code, "name": name, "max_object_bytes": max_object_bytes, "new_id": new_id},
        )
        return int(new_id.getvalue()[0])

    def update_provider(self, row: ProviderRow) -> None:
        self._conn.cursor().execute(
            _UPDATE_PROVIDER,
            {
                "name": row.provider_name,
                "max_object_bytes": row.max_object_bytes,
                "active": _flag(row.is_active),
                "provider_id": row.provider_id,
            },
        )

    # --- regions -----------------------------------------------------------------------------
    def list_regions(self, provider_id: int | None = None) -> list[RegionRow]:
        if provider_id is None:
            return [_region(r) for r in self._all(_LIST_REGIONS)]
        return [
            _region(r) for r in self._all(_LIST_REGIONS_FOR_PROVIDER, {"provider_id": provider_id})
        ]

    def get_region(self, region_id: int) -> RegionRow | None:
        rows = self._all(_GET_REGION, {"region_id": region_id})
        return _region(rows[0]) if rows else None

    def create_region(
        self,
        provider_id: int,
        region_code: str,
        bucket_name: str,
        reliability_score: Decimal,
        security_score: Decimal,
        egress_usd_per_gb: Decimal,
        is_illustrative: bool,
    ) -> int:
        cursor = self._conn.cursor()
        new_id = cursor.var(int)
        cursor.execute(
            _INSERT_REGION,
            {
                "provider_id": provider_id,
                "region_code": region_code,
                "bucket_name": bucket_name,
                "reliability": reliability_score,
                "security": security_score,
                "egress": egress_usd_per_gb,
                "illustrative": _flag(is_illustrative),
                "new_id": new_id,
            },
        )
        return int(new_id.getvalue()[0])

    def update_region(self, row: RegionRow) -> None:
        self._conn.cursor().execute(
            _UPDATE_REGION,
            {
                "bucket_name": row.bucket_name,
                "reliability": row.reliability_score,
                "security": row.security_score,
                "egress": row.egress_usd_per_gb,
                "health": row.health_status,
                "active": _flag(row.is_active),
                "region_id": row.region_id,
            },
        )

    # --- pricing -----------------------------------------------------------------------------
    def list_pricing(self, region_id: int | None = None) -> list[PricingRow]:
        if region_id is None:
            return [_pricing(r) for r in self._all(_LIST_PRICING)]
        return [_pricing(r) for r in self._all(_LIST_PRICING_FOR_REGION, {"region_id": region_id})]

    def upsert_pricing(self, row: PricingRow) -> None:
        self._conn.cursor().execute(
            _UPSERT_PRICING,
            {
                "region_id": row.region_id,
                "tier_code": row.tier_code,
                "native_class_name": row.native_class_name,
                "storage": row.storage_usd_per_gb_month,
                "retrieval": row.retrieval_usd_per_gb,
                "put_1k": row.put_usd_per_1k,
                "get_1k": row.get_usd_per_1k,
                "min_days": row.min_storage_days,
                "illustrative": _flag(row.is_illustrative),
            },
        )

    # --- read model for the selection engine -------------------------------------------------
    def candidates(self, tier: str) -> list[Candidate]:
        latencies: dict[int, list[Decimal]] = {}
        for region_id, latency in self._all(_LATENCY_SAMPLES, {"samples": LATENCY_SAMPLES}):
            latencies.setdefault(int(region_id), []).append(_dec(latency))

        result: list[Candidate] = []
        for r in self._all(_CANDIDATES, {"tier": tier}):
            pricing = None
            if r[13] is not None:
                pricing = TierPricing(
                    r[13], _dec(r[14]), _dec(r[15]), _dec(r[16]), _dec(r[17]), int(r[18])
                )
            samples = latencies.get(int(r[0]))
            result.append(
                Candidate(
                    region_id=int(r[0]),
                    provider_id=int(r[1]),
                    provider_code=r[2],
                    provider_name=r[3],
                    region_code=r[4],
                    provider_active=_yes(r[5]),
                    region_active=_yes(r[6]),
                    health_status=r[7],
                    reliability_score=_dec(r[8]),
                    security_score=_dec(r[9]),
                    max_object_bytes=int(r[10]),
                    egress_usd_per_gb=_dec(r[11]),
                    is_illustrative=_yes(r[12]),
                    pricing=pricing,
                    latency_ms=median(samples) if samples else None,
                )
            )
        return result

    def weight_profiles(self) -> tuple[dict[str, Weights], str | None]:
        profiles: dict[str, Weights] = {}
        default: str | None = None
        for row in self._all(_WEIGHT_PROFILES):
            profiles[row[0]] = Weights(*(_dec(v) for v in row[1:6]))
            if row[6] == "Y":
                default = row[0]
        return profiles, default

    def policy(self) -> Policy:
        security = dict(DEFAULT_POLICY.min_security)
        reliability = dict(DEFAULT_POLICY.min_reliability)
        for key, value in self._all(_POLICY_ROWS):
            if key.startswith(_SECURITY_PREFIX):
                security[key[len(_SECURITY_PREFIX) :]] = _dec(value)
            elif key.startswith(_RELIABILITY_PREFIX):
                reliability[key[len(_RELIABILITY_PREFIX) :]] = _dec(value)
        return Policy(security, reliability)
