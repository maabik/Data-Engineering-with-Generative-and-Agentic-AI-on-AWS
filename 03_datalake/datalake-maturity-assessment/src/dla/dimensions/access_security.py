"""Access Control & Security dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore

    s3 = snapshot.get("s3", {}) or {}
    kms = snapshot.get("kms", {}) or {}
    lf = snapshot.get("lakeformation", {}) or {}

    inspected = s3.get("inspected", []) or []
    total = max(1, len(inspected))

    encrypted = 0
    kms_encrypted = 0
    cmk_encrypted = 0
    pab_strict = 0
    for b in inspected:
        algos = [(e or {}).get("SSEAlgorithm") for e in b.get("encryption", [])]
        if any(a for a in algos):
            encrypted += 1
        if any(a == "aws:kms" or a == "aws:kms:dsse" for a in algos):
            kms_encrypted += 1
            # CMK if KMSMasterKeyID present
            if any((e or {}).get("KMSMasterKeyID") for e in b.get("encryption", [])):
                cmk_encrypted += 1
        pab = b.get("public_access_block", {}) or {}
        if all(pab.get(k) for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")):
            pab_strict += 1

    cmk_count = kms.get("customer_managed_count", 0)
    lf_admins = lf.get("admin_count", 0)
    lf_perms = lf.get("permission_count", 0)
    lf_col_perms = lf.get("column_permission_count", 0)
    lf_tag_count = lf.get("lf_tag_count", 0)
    lf_tag_perms = lf.get("lftag_permission_count", 0)
    iam_passthrough = lf.get("iam_passthrough_default", True)

    evidence = [
        f"Lake buckets inspected: {len(inspected)} (encrypted: {encrypted}, KMS: {kms_encrypted}, CMK-backed: {cmk_encrypted}, strict-PAB: {pab_strict})",
        f"Customer-managed KMS keys in account: {cmk_count}",
        f"Lake Formation: {lf_admins} admin(s), {lf_perms} permissions ({lf_col_perms} column-level, {lf_tag_perms} LF-Tag-based), {lf_tag_count} LF-Tags",
        f"IAM_ALLOWED_PRINCIPALS default permission: {'present (passthrough mode — LF not enforced)' if iam_passthrough else 'removed (LF enforced)'}",
    ]

    level = 1
    if encrypted >= 0.5 * total:
        level = 2
    if kms_encrypted >= 0.5 * total and cmk_count > 0 and lf_perms > 0 and not iam_passthrough:
        level = 3
    if level >= 3 and lf_tag_count > 0 and lf_tag_perms > 0:
        level = 4
    if level >= 4 and lf_col_perms > 0 and pab_strict >= 0.8 * total:
        level = 5

    if level == 1:
        top_gap = "Enable default SSE on all lake buckets and remove public-access exposure."
    elif level == 2:
        top_gap = "Switch to SSE-KMS with a customer-managed key, then enable Lake Formation grants."
    elif level == 3:
        top_gap = "Adopt LF-Tags for tag-based access control across databases and tables."
    elif level == 4:
        top_gap = "Add column-level filters and tighten public-access blocks across all lake buckets."
    else:
        top_gap = "—"

    return DimensionScore(name="access_security", level=level, evidence=evidence, top_gap=top_gap)
