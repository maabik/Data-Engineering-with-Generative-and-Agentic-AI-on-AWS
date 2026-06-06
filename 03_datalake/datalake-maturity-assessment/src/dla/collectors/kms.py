"""KMS collector — distinguish customer-managed from AWS-managed keys."""
from __future__ import annotations

from typing import Any

from ._util import make_client, paginate, safe_call


def collect_kms(region: str, profile: str | None = None) -> dict[str, Any]:
    kms = make_client("kms", region, profile)
    keys = paginate(kms, "list_keys", "Keys")
    cmk_count = 0
    aws_managed_count = 0
    sample_cmks = []
    for k in keys[:100]:  # bounded — per-key DescribeKey is N calls
        meta = safe_call(kms.describe_key, KeyId=k["KeyId"], default=None)
        if not meta:
            continue
        md = meta.get("KeyMetadata", {})
        if md.get("KeyManager") == "CUSTOMER":
            cmk_count += 1
            if len(sample_cmks) < 10:
                sample_cmks.append(md.get("KeyId"))
        elif md.get("KeyManager") == "AWS":
            aws_managed_count += 1
    return {
        "total_keys_listed": len(keys),
        "customer_managed_count": cmk_count,
        "aws_managed_count": aws_managed_count,
        "sample_cmk_ids": sample_cmks,
    }
