"""Firehose data transformation Lambda handler.

Each Firehose record is a base64-encoded JSON line (Bedrock invocation log).
The handler:
  1. Decodes and parses each record.
  2. Runs Comprehend PII detection on input + output text.
  3. Redacts/tokenizes PII spans (reversible mapping in token store).
  4. Applies sampling policy.
  5. Enriches with requestMetadata fields.
  6. Returns transformed (or dropped) records to Firehose.

Firehose record result statuses:
  - "Ok"        → transformed record forwarded to destination
  - "Dropped"   → record intentionally discarded (sampling)
  - "ProcessingFailed" → record sent to error prefix
"""

from __future__ import annotations

import base64
import json
import logging

import parser as _parser
import pii as _pii
import sampling as _sampling

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _decode_record(data: str) -> dict:
    raw_bytes = base64.b64decode(data)
    return json.loads(raw_bytes.decode("utf-8"))


def _encode_record(record: dict) -> str:
    line = json.dumps(record, default=str) + "\n"
    return base64.b64encode(line.encode("utf-8")).decode("utf-8")


def _process_one(firehose_record: dict) -> dict:
    record_id = firehose_record["recordId"]
    try:
        raw = _decode_record(firehose_record["data"])
    except Exception as exc:
        logger.error("Failed to decode record %s: %s", record_id, exc)
        return {"recordId": record_id, "result": "ProcessingFailed", "data": firehose_record["data"]}

    try:
        parsed = _parser.parse_record(raw)
    except Exception as exc:
        logger.error("Failed to parse record %s: %s", record_id, exc)
        return {"recordId": record_id, "result": "ProcessingFailed", "data": firehose_record["data"]}

    if not parsed:
        # Unknown schema type — drop silently
        return {"recordId": record_id, "result": "Dropped", "data": firehose_record["data"]}

    request_id = parsed.get("request_id", record_id)

    # PII detection on input
    try:
        input_pii = _pii.detect_and_redact(parsed.pop("_input_text_raw", ""), request_id + "_input")
        parsed["input_text_redacted"] = input_pii.redacted_text
        parsed["pii_detected"] = input_pii.detected
        parsed["pii_entity_types"] = input_pii.entity_types
        parsed["pii_token_ids"] = input_pii.token_ids
    except Exception as exc:
        logger.warning("PII detection failed for input of %s: %s", record_id, exc)

    # PII detection on output (accumulate entity types + token ids)
    try:
        output_pii = _pii.detect_and_redact(parsed.pop("_output_text_raw", ""), request_id + "_output")
        parsed["output_text_redacted"] = output_pii.redacted_text
        if output_pii.detected:
            parsed["pii_detected"] = True
            parsed["pii_entity_types"] = list(set(parsed["pii_entity_types"] + output_pii.entity_types))
            parsed["pii_token_ids"] = parsed["pii_token_ids"] + output_pii.token_ids
    except Exception as exc:
        logger.warning("PII detection failed for output of %s: %s", record_id, exc)

    # Sampling policy
    keep = _sampling.should_keep(parsed)
    parsed["sampled"] = keep

    if not keep:
        return {"recordId": record_id, "result": "Dropped", "data": firehose_record["data"]}

    return {
        "recordId": record_id,
        "result": "Ok",
        "data": _encode_record(parsed),
    }


def handler(event: dict, context) -> dict:
    records = event.get("records", [])
    results = [_process_one(r) for r in records]
    logger.info(
        "Processed %d records: ok=%d dropped=%d failed=%d",
        len(results),
        sum(1 for r in results if r["result"] == "Ok"),
        sum(1 for r in results if r["result"] == "Dropped"),
        sum(1 for r in results if r["result"] == "ProcessingFailed"),
    )
    return {"records": results}
