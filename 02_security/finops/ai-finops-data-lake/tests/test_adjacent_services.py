"""Tests for pipeline/sources/adjacent_services.py"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fixtures.sample_records import CUR_RECORD
from pipeline.sources.adjacent_services import (
    _service_category, filter_from_cur_records, SERVICE_CATEGORY_MAP
)
BEDROCK_SERVICE_CODES = set(SERVICE_CATEGORY_MAP.keys())


class TestServiceCategory:
    def test_bedrock_kb(self):
        assert _service_category("AmazonBedrock", "USE1-KnowledgeBaseRetrieval") == "bedrock_kb"

    def test_bedrock_guardrail(self):
        assert _service_category("AmazonBedrock", "USE1-BedrockGuardrailEvaluations") == "bedrock_guardrail"

    def test_bedrock_default(self):
        assert _service_category("AmazonBedrock", "USE1-Bedrock:InputTokens") == "bedrock"

    def test_opensearch(self):
        assert _service_category("AmazonOpenSearchService", "") == "opensearch"
        assert _service_category("AmazonOpenSearchServerless", "") == "opensearch"

    def test_s3_vectors(self):
        assert _service_category("AmazonS3", "USE1-VectorBucketRequests") == "s3_vectors"

    def test_s3_generic(self):
        assert _service_category("AmazonS3", "USE1-DataTransfer-Out-Bytes") == "s3"

    def test_agentcore(self):
        assert _service_category("AmazonBedrockAgentCore", "") == "agentcore"

    def test_unknown_service(self):
        cat = _service_category("AmazonSomeOtherService", "")
        assert cat == "amazonsomeotherservice"


class TestFilterFromCurRecords:
    def test_bedrock_record_included(self):
        records = list(filter_from_cur_records([CUR_RECORD]))
        assert len(records) == 1
        assert records[0]["service_category"] == "bedrock"

    def test_non_ai_service_excluded(self):
        non_ai = {**CUR_RECORD, "product_code": "AmazonEC2"}
        records = list(filter_from_cur_records([non_ai]))
        assert records == []

    def test_opensearch_included(self):
        os_rec = {**CUR_RECORD, "product_code": "AmazonOpenSearchService",
                  "usage_type": "ESInstance"}
        records = list(filter_from_cur_records([os_rec]))
        assert records[0]["service_category"] == "opensearch"

    def test_kb_labeled_correctly(self):
        kb_rec = {**CUR_RECORD, "product_code": "AmazonBedrock",
                  "usage_type": "USE1-KnowledgeBaseIngestionSync"}
        records = list(filter_from_cur_records([kb_rec]))
        assert records[0]["service_category"] == "bedrock_kb"

    def test_guardrail_labeled_correctly(self):
        gr_rec = {**CUR_RECORD, "product_code": "AmazonBedrock",
                  "usage_type": "USE1-GuardrailEvaluations"}
        records = list(filter_from_cur_records([gr_rec]))
        assert records[0]["service_category"] == "bedrock_guardrail"

    def test_source_field_set(self):
        records = list(filter_from_cur_records([CUR_RECORD]))
        assert records[0]["source"].startswith("cur_")

    def test_empty_input(self):
        assert list(filter_from_cur_records([])) == []
