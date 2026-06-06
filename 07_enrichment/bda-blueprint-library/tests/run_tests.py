#!/usr/bin/env python3
"""Run each blueprint against its labeled corpus document and diff results.

Pipeline per doc:
  1. Read build/blueprint-arns.json to find the blueprint ARN + bucket.
  2. InvokeDataAutomationAsync with the doc's S3 URI and that single blueprint
     pinned in the `blueprints` list.
  3. Poll GetDataAutomationStatus until success/failure.
  4. List the output S3 prefix, find result.json, download and parse.
  5. Diff inferred fields against corpus/<slug>/expected.json. For string fields
     we use a normalized substring/equality check; for numbers an absolute or
     relative tolerance; arrays compare item count + per-item field-level.
  6. Print a per-doc summary and write tests/results.json.

Failures don't abort — every doc runs and we print a final pass-rate.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "blueprint-arns.json"
CORPUS = ROOT / "corpus"
RESULTS = ROOT / "tests" / "results.json"

POLL_SECONDS = 5
POLL_TIMEOUT = 600  # 10 minutes per doc


def _load_cfg() -> dict[str, Any]:
    if not BUILD.exists():
        sys.exit(f"missing {BUILD} — run deploy.py first")
    return json.loads(BUILD.read_text())


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _str_match(expected: str, actual: Any) -> bool:
    if actual is None:
        return False
    e = _norm(expected)
    a = _norm(actual)
    if not e:
        return not a
    return e == a or e in a or a in e


def _num_match(expected: float, actual: Any, *, abs_tol: float = 0.01, rel_tol: float = 0.005) -> bool:
    try:
        a = float(actual)
    except (TypeError, ValueError):
        return False
    if abs(a - expected) <= abs_tol:
        return True
    if expected != 0 and abs(a - expected) / abs(expected) <= rel_tol:
        return True
    return False


def _bool_match(expected: bool, actual: Any) -> bool:
    if isinstance(actual, bool):
        return actual == expected
    if isinstance(actual, str):
        return actual.strip().lower() in (("true", "yes", "y", "1") if expected else ("false", "no", "n", "0", ""))
    return False


def _field_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool):
        return _bool_match(expected, actual)
    if isinstance(expected, (int, float)):
        return _num_match(float(expected), actual)
    if isinstance(expected, str):
        return _str_match(expected, actual)
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        # Just checks equal length and per-item field match for object items.
        if len(actual) != len(expected):
            return False
        for ex_item, ac_item in zip(expected, actual):
            if isinstance(ex_item, dict):
                for k, v in ex_item.items():
                    if not _field_match(v, ac_item.get(k) if isinstance(ac_item, dict) else None):
                        return False
            elif not _field_match(ex_item, ac_item):
                return False
        return True
    return expected == actual


def _flatten_to_dict(payload: dict) -> dict[str, Any]:
    """BDA result.json wraps the inferred fields. The structure is:

        {
          "matched_blueprint": {...},
          "document": {...},
          "inference_result": { "<field>": <value>, ... },
          "explainability_info": [{ "<field>": {...} }],
          ...
        }

    We want the flat inference_result map.
    """
    if "inference_result" in payload:
        return payload["inference_result"]
    # Older shape: "extracted_data" → "fields"
    if "extracted_data" in payload and "fields" in payload["extracted_data"]:
        return payload["extracted_data"]["fields"]
    return payload


def _wait_for_job(runtime, invocation_arn: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        resp = runtime.get_data_automation_status(invocationArn=invocation_arn)
        status = resp["status"]
        if status in ("Success", "ServiceError", "ClientError"):
            return resp
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"BDA job {invocation_arn} did not finish within {POLL_TIMEOUT}s")


def _find_result_object(s3, output_uri: str) -> tuple[str, str] | None:
    """Walk the output prefix and return (bucket, key) of the per-asset result.json."""
    assert output_uri.startswith("s3://")
    bucket, _, prefix = output_uri[len("s3://") :].partition("/")
    paginator = s3.get_paginator("list_objects_v2")
    candidates: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # BDA writes per-asset/.../custom_output/<id>/result.json
            if key.endswith("/result.json") and "custom_output" in key:
                candidates.append(key)
    if not candidates:
        # fall back to any result.json under the prefix
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith("/result.json"):
                    candidates.append(obj["Key"])
    if not candidates:
        return None
    return bucket, sorted(candidates)[0]


def _process_doc(slug: str, cfg: dict[str, Any]) -> dict[str, Any]:
    region = cfg["region"]
    bucket = cfg["bucket"]
    blueprint_arn = cfg["blueprints"][slug]
    profile_arn = cfg["data_automation_profile_arn"]

    pdf_path = next((CORPUS / slug).glob("*.pdf"))
    expected = json.loads((CORPUS / slug / "expected.json").read_text())

    s3_uri_in = f"s3://{bucket}/corpus/{slug}/{pdf_path.name}"
    s3_uri_out = f"s3://{bucket}/runs/{slug}/{int(time.time())}"

    runtime = boto3.client("bedrock-data-automation-runtime", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    print(f"\n[{slug}] invoke {s3_uri_in}")
    resp = runtime.invoke_data_automation_async(
        inputConfiguration={"s3Uri": s3_uri_in},
        outputConfiguration={"s3Uri": s3_uri_out},
        blueprints=[{"blueprintArn": blueprint_arn, "stage": "LIVE"}],
        dataAutomationProfileArn=profile_arn,
    )
    invocation_arn = resp["invocationArn"]
    print(f"[{slug}] invocationArn={invocation_arn}")

    status = _wait_for_job(runtime, invocation_arn)
    print(f"[{slug}] status={status['status']} duration={status.get('jobDurationInSeconds','?')}s")
    if status["status"] != "Success":
        return {
            "slug": slug,
            "status": status["status"],
            "error": status.get("errorMessage"),
            "fields_total": len(expected),
            "fields_matched": 0,
            "match_rate": 0.0,
        }

    output_uri = status["outputConfiguration"]["s3Uri"]
    # BDA returns the s3 uri to the job_metadata.json, not the result.json.
    # Walk the prefix to find the result.json the blueprint wrote.
    metadata_prefix = output_uri.rsplit("/", 1)[0]
    found = _find_result_object(s3, metadata_prefix)
    if not found:
        return {
            "slug": slug,
            "status": "NoResult",
            "error": f"no result.json under {metadata_prefix}",
            "fields_total": len(expected),
            "fields_matched": 0,
            "match_rate": 0.0,
        }
    rbucket, rkey = found
    body = s3.get_object(Bucket=rbucket, Key=rkey)["Body"].read()
    payload = json.loads(body)
    inferred = _flatten_to_dict(payload)

    matches: dict[str, bool] = {}
    for field, exp in expected.items():
        actual = inferred.get(field)
        matches[field] = _field_match(exp, actual)
    matched = sum(1 for v in matches.values() if v)
    total = len(matches)
    rate = matched / total if total else 0.0
    print(f"[{slug}] matched {matched}/{total} ({rate:.0%})")
    misses = [k for k, v in matches.items() if not v]
    if misses:
        print(f"[{slug}] misses: {', '.join(misses)}")

    return {
        "slug": slug,
        "status": "Success",
        "result_s3": f"s3://{rbucket}/{rkey}",
        "fields_total": total,
        "fields_matched": matched,
        "match_rate": round(rate, 3),
        "field_matches": matches,
        "inferred": inferred,
    }


def main() -> int:
    cfg = _load_cfg()
    summary = []
    for slug in cfg["blueprints"]:
        try:
            summary.append(_process_doc(slug, cfg))
        except Exception as exc:
            print(f"[{slug}] ERROR {exc!r}")
            summary.append({"slug": slug, "status": "Exception", "error": str(exc)})

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(summary, indent=2))

    print("\n=== Summary ===")
    total_fields = sum(r.get("fields_total", 0) for r in summary)
    matched_fields = sum(r.get("fields_matched", 0) for r in summary)
    for r in summary:
        rate = r.get("match_rate", 0.0)
        print(f"  {r['slug']:18s} {r['status']:10s} {r.get('fields_matched',0):3d}/{r.get('fields_total',0):<3d}  {rate:.0%}")
    overall = matched_fields / total_fields if total_fields else 0.0
    print(f"\nOverall: {matched_fields}/{total_fields} ({overall:.0%})")
    print(f"Results written to {RESULTS}")
    return 0 if overall > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
