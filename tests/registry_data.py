"""Loads the Chunk 1 reference data (sql/seed_data.sql) into a FakeDatabase.

The numbers are copied from that SQL file and are ILLUSTRATIVE simulation values.
"""

from __future__ import annotations

from decimal import Decimal as D

from app.providers.models import PricingRow, ProviderRow, RegionRow
from tests.fakes import FakeDatabase

TIB = 1024**4

PROVIDERS = [  # id, code, name, max_object_bytes
    (1, "AWS", "AWS", 5 * TIB),
    (2, "AZURE", "Azure", 190 * TIB),
    (3, "GCP", "Google Cloud", 5 * TIB),
]
REGIONS = [  # id, provider_id, code, bucket, reliability, security, egress, base GET latency ms
    (1, 1, "ap-south-1", "project-bucket", "9.50", "9.20", "0.0900", 40, "1.00"),
    (2, 1, "us-east-1", "cmm-aws-us-east-1", "9.60", "9.20", "0.0900", 220, "0.92"),
    (3, 2, "Central India", "presentation-bucket", "9.40", "9.30", "0.0870", 45, "1.00"),
    (4, 2, "East US", "cmm-azure-eastus", "9.45", "9.30", "0.0870", 225, "0.92"),
    (5, 3, "asia-south1", "data-bucket", "9.30", "9.10", "0.1200", 42, "1.00"),
    (6, 3, "us-central1", "cmm-gcp-us-central1", "9.35", "9.10", "0.1200", 210, "0.92"),
]
BASE_PRICES = {  # provider_id: tier: (native, storage, retrieval, put/1k, get/1k, min_days)
    1: {"HOT": ("STANDARD", "0.0250", "0.0000", "0.0050", "0.00040", 0),
        "COOL": ("STANDARD_IA", "0.0138", "0.0100", "0.0100", "0.00100", 30),
        "COLD": ("GLACIER_IR", "0.0050", "0.0300", "0.0200", "0.01000", 90),
        "ARCHIVE": ("DEEP_ARCHIVE", "0.0020", "0.0200", "0.0500", "0.00040", 180)},
    2: {"HOT": ("HOT", "0.0240", "0.0000", "0.0055", "0.00044", 0),
        "COOL": ("COOL", "0.0150", "0.0100", "0.0100", "0.00100", 30),
        "COLD": ("COLD", "0.0045", "0.0300", "0.0180", "0.01000", 90),
        "ARCHIVE": ("ARCHIVE", "0.0020", "0.0220", "0.0500", "0.00500", 180)},
    3: {"HOT": ("STANDARD", "0.0230", "0.0000", "0.0050", "0.00040", 0),
        "COOL": ("NEARLINE", "0.0100", "0.0100", "0.0100", "0.00100", 30),
        "COLD": ("COLDLINE", "0.0040", "0.0200", "0.0100", "0.00500", 90),
        "ARCHIVE": ("ARCHIVE", "0.0012", "0.0500", "0.0500", "0.05000", 365)},
}  # fmt: skip


def seed_registry(db: FakeDatabase) -> None:
    s = db.state
    for pid, code, name, max_bytes in PROVIDERS:
        s["providers"][pid] = ProviderRow(pid, code, name, max_bytes, True)
    s["next_provider_id"] = len(PROVIDERS)
    for rid, pid, code, bucket, rel, sec, egress, latency, mult in REGIONS:
        s["regions"][rid] = RegionRow(
            rid, pid, code, bucket, D(rel), D(sec), D(egress), "UP", None, True, True
        )
        s["latencies"][rid] = [D(latency)]
        for tier, (native, storage, retrieval, put, get, days) in BASE_PRICES[pid].items():
            m = D(mult)
            s["pricing"][(rid, tier)] = PricingRow(
                rid, tier, native, round(D(storage) * m, 6), round(D(retrieval) * m, 6),
                round(D(put) * m, 6), round(D(get) * m, 6), days, True,
            )  # fmt: skip
    s["next_region_id"] = len(REGIONS)
