"""Join stage: correlates Bedrock invocation records with CUR line items.

Matching strategy (in priority order):
1. Exact request_id match against CUR line_item_resource_id
2. Date + model_id + account_id + approximate token count window (±10%)
3. Date + account_id + model_id (aggregate join — costs shared proportionally)

Records that cannot be matched get actual_cost_usd=None and joined=False.
CUR costs that match are propagated into estimated_cost_usd as actual_cost_usd.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

logger = logging.getLogger(__name__)

_TOKEN_TOLERANCE = 0.10  # ±10% for fuzzy token matching


def _bucket_key(rec: dict) -> tuple:
    return (rec.get("date", ""), rec.get("account_id", ""), rec.get("model_id", ""))


def build_cur_index(cur_records: Iterable[dict]) -> dict:
    """Build lookup structures from CUR records."""
    by_request_id: dict[str, dict] = {}
    by_bucket: dict[tuple, list[dict]] = defaultdict(list)

    for rec in cur_records:
        rid = rec.get("request_id", "")
        if rid:
            by_request_id[rid] = rec
        by_bucket[_bucket_key(rec)].append(rec)

    return {"by_request_id": by_request_id, "by_bucket": dict(by_bucket)}


def _fuzzy_token_match(inv_tokens: int, cur_tokens: int) -> bool:
    if inv_tokens == 0 or cur_tokens == 0:
        return False
    ratio = abs(inv_tokens - cur_tokens) / max(inv_tokens, cur_tokens)
    return ratio <= _TOKEN_TOLERANCE


def join_record(record: dict, cur_index: dict) -> dict:
    """Enrich a single invocation record with CUR cost data."""
    record = dict(record)

    # 1. Exact request_id match
    rid = record.get("request_id", "")
    if rid and rid in cur_index["by_request_id"]:
        cur = cur_index["by_request_id"][rid]
        record["actual_cost_usd"] = cur.get("actual_cost_usd", cur.get("estimated_cost_usd"))
        record["cur_line_item_id"] = cur.get("cur_line_item_id", "")
        record["iam_principal"] = record.get("iam_principal") or cur.get("iam_principal", "")
        record["joined"] = True
        record["join_method"] = "exact_request_id"
        return record

    # 2. Fuzzy token match within same date+account+model bucket
    bucket = _bucket_key(record)
    candidates = cur_index["by_bucket"].get(bucket, [])
    inv_tokens = record.get("total_tokens", 0)

    for cur in candidates:
        if _fuzzy_token_match(inv_tokens, cur.get("total_tokens", 0)):
            record["actual_cost_usd"] = cur.get("actual_cost_usd", cur.get("estimated_cost_usd"))
            record["cur_line_item_id"] = cur.get("cur_line_item_id", "")
            record["iam_principal"] = record.get("iam_principal") or cur.get("iam_principal", "")
            record["joined"] = True
            record["join_method"] = "fuzzy_token"
            return record

    # 3. No CUR match — keep estimated cost
    record["actual_cost_usd"] = None
    record["joined"] = False
    record["join_method"] = "none"
    return record


def join_all(
    invocation_records: Iterable[dict],
    cur_records: Iterable[dict],
) -> list[dict]:
    """Join invocation records with CUR. Returns enriched list."""
    cur_index = build_cur_index(cur_records)
    results = []
    matched = unmatched = 0
    for rec in invocation_records:
        joined = join_record(rec, cur_index)
        results.append(joined)
        if joined.get("joined"):
            matched += 1
        else:
            unmatched += 1
    logger.info("Join complete: %d matched, %d unmatched", matched, unmatched)
    return results
