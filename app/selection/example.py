"""The worked example used by docs/03-selection-engine.md, scripts/worked_example.py and tests.

*** EVERY PRICE, SCORE AND LATENCY BELOW IS ILLUSTRATIVE (made up for demonstration). ***
They are NOT vendor prices and not measurements. They were chosen so that the arithmetic is easy
to follow by hand and so that the cheapest provider is not the best overall.

File: 10 GiB, MEDIUM security, tier HOT, kept 12 months, read 4 times a month.
"""

from __future__ import annotations

from decimal import Decimal as D

from app.selection.models import Candidate, FileRequest, TierPricing

EXAMPLE_REQUEST = FileRequest(
    file_size_bytes=10 * 1024**3,
    security_level="MEDIUM",
    expected_accesses_per_month=D(4),
    retention_months=D(12),
    tier="HOT",
)


def _candidate(
    n: int, code: str, name: str, region: str, *, storage, put, get, egress, latency, rel, sec
) -> Candidate:
    return Candidate(
        region_id=n,
        provider_id=n,
        provider_code=code,
        provider_name=name,
        region_code=region,
        provider_active=True,
        region_active=True,
        health_status="UP",
        reliability_score=D(rel),
        security_score=D(sec),
        max_object_bytes=5 * 1024**4,
        egress_usd_per_gb=D(egress),
        pricing=TierPricing("HOT", D(storage), D("0"), D(put), D(get), 0),
        latency_ms=D(latency),
        is_illustrative=True,
    )


EXAMPLE_CANDIDATES = (
    _candidate(1, "AWS", "AWS", "ap-south-1",
               storage="0.023", put="0.005", get="0.0004", egress="0.09",
               latency="40", rel="9.5", sec="9.2"),
    _candidate(2, "AZURE", "Azure", "Central India",
               storage="0.020", put="0.0055", get="0.00044", egress="0.087",
               latency="60", rel="9.4", sec="9.3"),
    _candidate(3, "GCP", "Google Cloud", "asia-south1",
               storage="0.026", put="0.005", get="0.0004", egress="0.12",
               latency="50", rel="9.3", sec="9.1"),
)  # fmt: skip
