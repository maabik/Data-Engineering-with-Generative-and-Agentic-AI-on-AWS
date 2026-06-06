"""Tests for pipeline/sources/inference_profiles.py"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pipeline.sources.inference_profiles import fetch_profile_map, enrich_record


def _make_bedrock(profiles: list[dict]):
    class FakeBedrock:
        def get_paginator(self, op):
            class P:
                def paginate(self, **kw):
                    return [{"inferenceProfileSummaries": profiles}]
            return P()

        def get_inference_profile(self, inferenceProfileIdentifier):
            for p in profiles:
                if p.get("inferenceProfileId") == inferenceProfileIdentifier:
                    return p
            raise KeyError(inferenceProfileIdentifier)

    return FakeBedrock()


SAMPLE_PROFILE = {
    "inferenceProfileId": "prof-001",
    "inferenceProfileName": "acme-claude",
    "inferenceProfileArn": "arn:aws:bedrock:us-east-1:123:inference-profile/prof-001",
    "type": "APPLICATION",
    "status": "ACTIVE",
    "models": [{"modelArn": "arn:aws:bedrock::anthropic:anthropic.claude-3-sonnet-20240229-v1:0"}],
    "tags": [
        {"key": "tenant_id", "value": "acme"},
        {"key": "Workload", "value": "chat-assistant"},
    ],
}


class TestFetchProfileMap:
    def test_basic_fetch(self):
        bedrock = _make_bedrock([SAMPLE_PROFILE])
        pmap = fetch_profile_map(bedrock_client=bedrock)
        assert "prof-001" in pmap
        p = pmap["prof-001"]
        assert p["inference_profile_name"] == "acme-claude"
        assert p["tenant_id"] == "acme"
        assert p["workload"] == "chat-assistant"

    def test_model_ids_extracted(self):
        bedrock = _make_bedrock([SAMPLE_PROFILE])
        pmap = fetch_profile_map(bedrock_client=bedrock)
        assert "anthropic.claude-3-sonnet-20240229-v1:0" in pmap["prof-001"]["model_ids"]

    def test_empty_profiles(self):
        bedrock = _make_bedrock([])
        pmap = fetch_profile_map(bedrock_client=bedrock)
        assert pmap == {}

    def test_missing_profile_id_skipped(self):
        profiles = [{**SAMPLE_PROFILE, "inferenceProfileId": ""}]
        bedrock = _make_bedrock(profiles)
        pmap = fetch_profile_map(bedrock_client=bedrock)
        assert pmap == {}


class TestEnrichRecord:
    def test_profile_enriched(self):
        pmap = {
            "prof-001": {
                "inference_profile_name": "acme-claude",
                "tenant_id": "acme",
                "workload": "chat",
            }
        }
        record = {"inference_profile_id": "prof-001", "tenant_id": "_unknown", "workload": ""}
        enriched = enrich_record(record, pmap)
        assert enriched["inference_profile_name"] == "acme-claude"
        assert enriched["tenant_id"] == "acme"
        assert enriched["workload"] == "chat"

    def test_no_profile_id_unchanged(self):
        pmap = {}
        record = {"inference_profile_id": "", "tenant_id": "myco", "workload": "w1"}
        enriched = enrich_record(record, pmap)
        assert enriched["tenant_id"] == "myco"

    def test_existing_tenant_not_overwritten(self):
        pmap = {"prof-001": {"inference_profile_name": "x", "tenant_id": "new", "workload": ""}}
        record = {"inference_profile_id": "prof-001", "tenant_id": "existing", "workload": ""}
        enriched = enrich_record(record, pmap)
        # tenant_id is only overwritten when it's "_unknown"
        assert enriched["tenant_id"] == "existing"
