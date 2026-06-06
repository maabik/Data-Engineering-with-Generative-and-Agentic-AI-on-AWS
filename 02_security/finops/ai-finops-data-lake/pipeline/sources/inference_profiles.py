"""Source (c): Application Inference Profile metadata from Bedrock.

Inference profiles let teams route traffic to a named profile (which maps to
one or more underlying models) and attach cost-allocation tags. This source
fetches all profiles in the account and returns a lookup dict that the join
stage uses to enrich invocation records with profile name + tags.

Bedrock API: bedrock.list_inference_profiles()
             bedrock.get_inference_profile(inferenceProfileIdentifier=...)
"""

from __future__ import annotations

import logging
from typing import Generator

import boto3

logger = logging.getLogger(__name__)


def _iter_profiles(bedrock_client) -> Generator[dict, None, None]:
    paginator = bedrock_client.get_paginator("list_inference_profiles")
    for page in paginator.paginate():
        for profile in page.get("inferenceProfileSummaries", []):
            yield profile


def fetch_profile_map(region: str | None = None, bedrock_client=None) -> dict[str, dict]:
    """Return {inferenceProfileId: {name, model_ids, tags, status, type}} mapping."""
    if bedrock_client is None:
        bedrock_client = boto3.client("bedrock", region_name=region)

    profile_map: dict[str, dict] = {}

    for summary in _iter_profiles(bedrock_client):
        profile_id = summary.get("inferenceProfileId", "")
        if not profile_id:
            continue

        try:
            detail = bedrock_client.get_inference_profile(
                inferenceProfileIdentifier=profile_id
            )
        except Exception as e:
            logger.warning("Could not fetch profile %s: %s", profile_id, e)
            detail = summary

        # Extract underlying model IDs from the models list
        model_ids = []
        for m in detail.get("models", []):
            arn = m.get("modelArn", "")
            # Standard ARN: arn:aws:bedrock:region:account:foundation-model/model-id
            if "/" in arn:
                model_ids.append(arn.split("/", 1)[-1])
            else:
                # Cross-region ARN: arn:aws:bedrock::provider:model-id  (6 colon-parts)
                # model ID = parts[-1], but full model ID may be "provider.model:version"
                # Keep everything after the 5th colon (parts[5:] joined)
                parts = arn.split(":")
                model_ids.append(":".join(parts[5:]) if len(parts) > 5 else arn)

        profile_map[profile_id] = {
            "inference_profile_id": profile_id,
            "inference_profile_name": detail.get("inferenceProfileName", ""),
            "inference_profile_arn": detail.get("inferenceProfileArn", ""),
            "type": detail.get("type", ""),
            "status": detail.get("status", ""),
            "model_ids": model_ids,
            "tags": {t["key"]: t["value"] for t in detail.get("tags", [])},
            "tenant_id": next(
                (t["value"] for t in detail.get("tags", [])
                 if t["key"].lower() in ("tenant", "tenantid", "tenant_id")),
                "",
            ),
            "workload": next(
                (t["value"] for t in detail.get("tags", [])
                 if t["key"].lower() in ("workload", "application")),
                "",
            ),
        }

    logger.info("Fetched %d inference profiles", len(profile_map))
    return profile_map


def enrich_record(record: dict, profile_map: dict[str, dict]) -> dict:
    """Add inference profile fields to an invocation record if a profile_id is present."""
    profile_id = record.get("inference_profile_id", "")
    if profile_id and profile_id in profile_map:
        p = profile_map[profile_id]
        record["inference_profile_name"] = p["inference_profile_name"]
        if not record.get("workload"):
            record["workload"] = p.get("workload", "")
        if record.get("tenant_id", "_unknown") == "_unknown" and p.get("tenant_id"):
            record["tenant_id"] = p["tenant_id"]
    return record
