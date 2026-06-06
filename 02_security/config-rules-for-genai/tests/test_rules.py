"""Tests for all 9 Config rule evaluate() functions.

All tests call evaluate() directly — no Lambda invocation, no AWS calls.
"""

import pytest
from lib.evaluator import Status
from tests.helpers import FakeConfigClient, make_event

# ─── helpers ─────────────────────────────────────────────────────────────────

def _eval(module_path, ci_config, resource_type, resource_id="rid", tags=None,
          rule_params=None, supplementary=None, ci_status="OK"):
    """Import a rule module, call evaluate(), return (status, annotation)."""
    import importlib
    mod = importlib.import_module(module_path)
    import json
    ci = {
        "resourceType": resource_type,
        "resourceId": resource_id,
        "configuration": json.dumps(ci_config) if isinstance(ci_config, dict) else ci_config,
        "configurationItemStatus": ci_status,
        "configurationItemCaptureTime": "2026-01-01T00:00:00Z",
        "tags": tags or {},
    }
    if supplementary:
        ci["supplementaryConfiguration"] = supplementary
    result = mod.evaluate(ci, rule_params or {})
    if isinstance(result, tuple):
        return result[0], result[1] if len(result) > 1 else ""
    return result, ""


RT = {
    "logging": "AWS::Bedrock::ModelInvocationLoggingConfiguration",
    "agent":   "AWS::Bedrock::Agent",
    "kb":      "AWS::Bedrock::KnowledgeBase",
    "url":     "AWS::Lambda::Url",
    "kms":     "AWS::KMS::Key",
    "s3":      "AWS::S3::Bucket",
    "trail":   "AWS::CloudTrail::Trail",
    "prov":    "AWS::Bedrock::ProvisionedModelThroughput",
    "fn":      "AWS::Lambda::Function",
}

MOD = {
    "logging":  "rules.bedrock_invocation_logging_enabled",
    "guardrail":"rules.bedrock_guardrail_attached",
    "kb":       "rules.bedrock_kb_encryption_cmk",
    "url":      "rules.lambda_url_auth_required",
    "kms":      "rules.kms_cmk_rotation_ai_workloads",
    "agent_lp": "rules.bedrock_agent_least_privilege",
    "s3":       "rules.s3_ai_data_no_public_access",
    "trail":    "rules.cloudtrail_bedrock_data_events",
    "tag":      "rules.bedrock_model_access_tag_required",
    "mcp":      "rules.mcp_server_auth_required",
}

# ─── 1. bedrock_invocation_logging_enabled ────────────────────────────────────

class TestBedrockInvocationLogging:
    def _r(self, cfg):
        return _eval(MOD["logging"], cfg, RT["logging"])

    def test_no_logging_config(self):
        s, _ = self._r({})
        assert s == Status.NON_COMPLIANT

    def test_logging_config_no_destinations(self):
        s, _ = self._r({"loggingConfig": {}})
        assert s == Status.NON_COMPLIANT

    def test_both_disabled(self):
        s, _ = self._r({"loggingConfig": {
            "cloudWatchConfig": {"enabled": False},
            "s3Config": {"enabled": False},
        }})
        assert s == Status.NON_COMPLIANT

    def test_cloudwatch_only(self):
        s, ann = self._r({"loggingConfig": {"cloudWatchConfig": {"enabled": True}}})
        assert s == Status.COMPLIANT
        assert "CloudWatch" in ann

    def test_s3_only(self):
        s, ann = self._r({"loggingConfig": {"s3Config": {"enabled": True}}})
        assert s == Status.COMPLIANT
        assert "S3" in ann

    def test_both_enabled(self):
        s, ann = self._r({"loggingConfig": {
            "cloudWatchConfig": {"enabled": True},
            "s3Config": {"enabled": True},
        }})
        assert s == Status.COMPLIANT
        assert "CloudWatch" in ann
        assert "S3" in ann

    def test_null_logging_config(self):
        s, _ = self._r({"loggingConfig": None})
        assert s == Status.NON_COMPLIANT


# ─── 2. bedrock_guardrail_attached ────────────────────────────────────────────

class TestBedrockGuardrailAttached:
    def _r(self, cfg):
        return _eval(MOD["guardrail"], cfg, RT["agent"])

    def test_no_guardrail(self):
        s, _ = self._r({"agentName": "my-agent"})
        assert s == Status.NON_COMPLIANT

    def test_empty_guardrail_id(self):
        s, _ = self._r({"guardrailConfiguration": {"guardrailId": ""}})
        assert s == Status.NON_COMPLIANT

    def test_guardrail_present(self):
        s, ann = self._r({"guardrailConfiguration": {"guardrailId": "grd-abc123", "guardrailVersion": "1"}})
        assert s == Status.COMPLIANT
        assert "grd-abc123" in ann

    def test_guardrail_draft_version(self):
        s, ann = self._r({"guardrailConfiguration": {"guardrailId": "grd-xyz", "guardrailVersion": "DRAFT"}})
        assert s == Status.COMPLIANT
        assert "DRAFT" in ann

    def test_missing_guardrail_configuration_key(self):
        s, _ = self._r({})
        assert s == Status.NON_COMPLIANT


# ─── 3. bedrock_kb_encryption_cmk ─────────────────────────────────────────────

class TestBedrockKbEncryptionCmk:
    def _r(self, cfg, params=None):
        return _eval(MOD["kb"], cfg, RT["kb"], rule_params=params)

    def test_no_kms_arn(self):
        s, _ = self._r({"name": "my-kb"})
        assert s == Status.NON_COMPLIANT

    def test_aws_managed_key(self):
        s, _ = self._r({"kmsKeyArn": "arn:aws:kms:us-west-2:123:key/aws/bedrock"})
        assert s == Status.NON_COMPLIANT

    def test_cmk(self):
        s, _ = self._r({"kmsKeyArn": "arn:aws:kms:us-west-2:123456789012:key/mrk-abc123"})
        assert s == Status.COMPLIANT

    def test_cmk_in_storage_config(self):
        s, _ = self._r({"storageConfiguration": {"kmsKeyArn": "arn:aws:kms:us-west-2:123456789012:key/mrk-abc"}})
        assert s == Status.COMPLIANT

    def test_required_prefix_mismatch(self):
        s, _ = self._r(
            {"kmsKeyArn": "arn:aws:kms:eu-west-1:123456789012:key/mrk-abc"},
            params={"required_key_prefix": "arn:aws:kms:us-west-2:"},
        )
        assert s == Status.NON_COMPLIANT

    def test_required_prefix_match(self):
        s, _ = self._r(
            {"kmsKeyArn": "arn:aws:kms:us-west-2:123456789012:key/mrk-abc"},
            params={"required_key_prefix": "arn:aws:kms:us-west-2:"},
        )
        assert s == Status.COMPLIANT

    def test_aws_managed_key_with_slash_pattern(self):
        s, _ = self._r({"kmsKeyArn": "arn:aws:kms:us-east-1:123:/aws/s3"})
        assert s == Status.NON_COMPLIANT


# ─── 4. lambda_url_auth_required ─────────────────────────────────────────────

class TestLambdaUrlAuthRequired:
    RT_URL = RT["url"]

    def _r(self, cfg, tags=None, params=None):
        return _eval(MOD["url"], cfg, self.RT_URL, tags=tags, rule_params=params)

    def test_auth_none_with_ai_tag(self):
        s, _ = self._r(
            {"authType": "NONE", "functionArn": "arn:..."},
            tags={"Workload": "MCP"},
        )
        assert s == Status.NON_COMPLIANT

    def test_auth_iam_with_ai_tag(self):
        s, _ = self._r(
            {"authType": "AWS_IAM", "functionArn": "arn:..."},
            tags={"Workload": "GenAI"},
        )
        assert s == Status.COMPLIANT

    def test_no_ai_tag_skipped(self):
        s, _ = self._r({"authType": "NONE"}, tags={})
        assert s == Status.NOT_APPLICABLE

    def test_no_tag_filter_evaluates_all(self):
        s, _ = self._r({"authType": "NONE"}, params={"tag_key": "", "tag_value": ""})
        assert s == Status.NON_COMPLIANT

    def test_no_tag_filter_iam_compliant(self):
        s, _ = self._r({"authType": "AWS_IAM"}, params={"tag_key": "", "tag_value": ""})
        assert s == Status.COMPLIANT

    def test_specific_tag_value_match(self):
        s, _ = self._r(
            {"authType": "NONE"},
            tags={"Workload": "MCP"},
            params={"tag_key": "Workload", "tag_value": "MCP"},
        )
        assert s == Status.NON_COMPLIANT

    def test_specific_tag_value_no_match(self):
        s, _ = self._r(
            {"authType": "NONE"},
            tags={"Workload": "Billing"},
            params={"tag_key": "Workload", "tag_value": "MCP"},
        )
        assert s == Status.NOT_APPLICABLE

    def test_unknown_auth_type(self):
        s, _ = self._r({"authType": "JWT"}, params={"tag_key": "", "tag_value": ""})
        assert s == Status.NON_COMPLIANT


# ─── 5. kms_cmk_rotation_ai_workloads ────────────────────────────────────────

class TestKmsCmkRotation:
    def _r(self, cfg, tags=None, params=None):
        return _eval(MOD["kms"], cfg, RT["kms"], tags=tags, rule_params=params)

    def test_aws_managed_key_skipped(self):
        s, _ = self._r({"keyManager": "AWS"}, tags={"Workload": "AI"})
        assert s == Status.NOT_APPLICABLE

    def test_no_ai_tag_skipped(self):
        s, _ = self._r({"keyManager": "CUSTOMER", "keyRotationEnabled": False}, tags={})
        assert s == Status.NOT_APPLICABLE

    def test_rotation_disabled(self):
        s, ann = self._r(
            {"keyManager": "CUSTOMER", "keyRotationEnabled": False, "multiRegion": False},
            tags={"Workload": "AI"},
        )
        assert s == Status.NON_COMPLIANT
        assert "rotation" in ann

    def test_multi_region(self):
        s, ann = self._r(
            {"keyManager": "CUSTOMER", "keyRotationEnabled": True, "multiRegion": True},
            tags={"Workload": "GenAI"},
        )
        assert s == Status.NON_COMPLIANT
        assert "multi-region" in ann

    def test_compliant(self):
        s, _ = self._r(
            {"keyManager": "CUSTOMER", "keyRotationEnabled": True, "multiRegion": False},
            tags={"Workload": "Bedrock"},
        )
        assert s == Status.COMPLIANT

    def test_allow_multi_region_param(self):
        s, _ = self._r(
            {"keyManager": "CUSTOMER", "keyRotationEnabled": True, "multiRegion": True},
            tags={"Workload": "AI"},
            params={"allow_multi_region": "true"},
        )
        assert s == Status.COMPLIANT

    def test_pending_deletion_skipped(self):
        s, _ = self._r(
            {"keyManager": "CUSTOMER", "keyState": "PendingDeletion"},
            tags={"Workload": "AI"},
        )
        assert s == Status.NOT_APPLICABLE

    def test_both_rotation_and_multiregion_findings(self):
        s, ann = self._r(
            {"keyManager": "CUSTOMER", "keyRotationEnabled": False, "multiRegion": True},
            tags={"Workload": "AI"},
        )
        assert s == Status.NON_COMPLIANT
        assert "rotation" in ann
        assert "multi-region" in ann


# ─── 6. bedrock_agent_least_privilege ────────────────────────────────────────

class TestBedrockAgentLeastPrivilege:
    def _r(self, cfg, params=None):
        return _eval(MOD["agent_lp"], cfg, RT["agent"], rule_params=params)

    def test_no_role_arn(self):
        s, _ = self._r({"agentName": "myagent"})
        assert s == Status.NON_COMPLIANT

    def test_admin_role(self):
        s, ann = self._r({"agentResourceRoleArn": "arn:aws:iam::123:role/AdminRole"})
        assert s == Status.NON_COMPLIANT
        assert "Admin" in ann

    def test_power_user_role(self):
        s, _ = self._r({"agentResourceRoleArn": "arn:aws:iam::123:role/PowerUserRole"})
        assert s == Status.NON_COMPLIANT

    def test_full_access_role(self):
        s, _ = self._r({"agentResourceRoleArn": "arn:aws:iam::123:role/FullAccessRole"})
        assert s == Status.NON_COMPLIANT

    def test_least_privilege_role(self):
        s, _ = self._r({"agentResourceRoleArn": "arn:aws:iam::123:role/bedrock-agent-invoker-role"})
        assert s == Status.COMPLIANT

    def test_custom_disallowed_pattern(self):
        s, _ = self._r(
            {"agentResourceRoleArn": "arn:aws:iam::123:role/SuperDangerousRole"},
            params={"disallowed_role_patterns": "Dangerous"},
        )
        assert s == Status.NON_COMPLIANT

    def test_custom_pattern_no_match(self):
        s, _ = self._r(
            {"agentResourceRoleArn": "arn:aws:iam::123:role/SafeRole"},
            params={"disallowed_role_patterns": "Dangerous"},
        )
        assert s == Status.COMPLIANT


# ─── 7. s3_ai_data_no_public_access ──────────────────────────────────────────

class TestS3AiDataNoPublicAccess:
    ALL_BPA = {
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    }

    def _r(self, bpa, tags=None, params=None):
        cfg = {"bucketName": "my-bucket", "publicAccessBlockConfiguration": bpa}
        return _eval(MOD["s3"], cfg, RT["s3"], tags=tags, rule_params=params)

    def test_all_enabled(self):
        s, _ = self._r(self.ALL_BPA, tags={"DataClassification": "AI"})
        assert s == Status.COMPLIANT

    def test_one_flag_missing(self):
        bpa = {**self.ALL_BPA, "BlockPublicAcls": False}
        s, ann = self._r(bpa, tags={"DataClassification": "Confidential"})
        assert s == Status.NON_COMPLIANT
        assert "BlockPublicAcls" in ann

    def test_no_bpa_config(self):
        s, _ = self._r({}, tags={"DataClassification": "AI"})
        assert s == Status.NON_COMPLIANT

    def test_wrong_tag_skipped(self):
        s, _ = self._r({}, tags={"DataClassification": "Public"})
        assert s == Status.NOT_APPLICABLE

    def test_no_tag_skipped_by_default(self):
        s, _ = self._r(self.ALL_BPA, tags={})
        assert s == Status.NOT_APPLICABLE

    def test_no_tag_filter_evaluates_all(self):
        s, _ = self._r({}, params={"tag_key": "", "tag_value": ""})
        assert s == Status.NON_COMPLIANT

    def test_all_four_flags_false(self):
        bpa = {k: False for k in self.ALL_BPA}
        s, ann = self._r(bpa, tags={"DataClassification": "Restricted"})
        assert s == Status.NON_COMPLIANT
        for flag in self.ALL_BPA:
            assert flag in ann


# ─── 8. cloudtrail_bedrock_data_events ───────────────────────────────────────

class TestCloudtrailBedrockDataEvents:
    def _r(self, cfg):
        return _eval(MOD["trail"], cfg, RT["trail"])

    def test_no_selectors(self):
        s, ann = self._r({"name": "my-trail"})
        assert s == Status.NON_COMPLIANT
        assert "data events" in ann

    def test_bedrock_resource_type_selector(self):
        s, _ = self._r({"name": "t", "eventSelectors": [
            {"dataResources": [{"type": "AWS::Bedrock::*", "values": ["arn:aws:bedrock:*"]}]}
        ]})
        assert s == Status.COMPLIANT

    def test_bedrock_arn_value_selector(self):
        s, _ = self._r({"name": "t", "eventSelectors": [
            {"dataResources": [{"type": "AWS::S3::Object", "values": ["arn:aws:bedrock:us-west-2:*"]}]}
        ]})
        assert s == Status.COMPLIANT

    def test_non_bedrock_selectors_only(self):
        s, _ = self._r({"name": "t", "eventSelectors": [
            {"dataResources": [{"type": "AWS::S3::Object", "values": ["arn:aws:s3:::my-bucket/"]}]}
        ]})
        assert s == Status.NON_COMPLIANT

    def test_advanced_selector_event_source(self):
        s, _ = self._r({"name": "t", "advancedEventSelectors": [
            {"fieldSelectors": [
                {"field": "eventSource", "equals": ["bedrock.amazonaws.com"]}
            ]}
        ]})
        assert s == Status.COMPLIANT

    def test_advanced_selector_resource_type(self):
        s, _ = self._r({"name": "t", "advancedEventSelectors": [
            {"fieldSelectors": [
                {"field": "resources.type", "equals": ["AWS::Bedrock::AgentAlias"]}
            ]}
        ]})
        assert s == Status.COMPLIANT

    def test_advanced_selector_non_bedrock(self):
        s, _ = self._r({"name": "t", "advancedEventSelectors": [
            {"fieldSelectors": [
                {"field": "eventSource", "startsWith": ["s3.amazonaws.com"]}
            ]}
        ]})
        assert s == Status.NON_COMPLIANT

    def test_bedrock_agent_runtime_source(self):
        s, _ = self._r({"name": "t", "advancedEventSelectors": [
            {"fieldSelectors": [
                {"field": "eventSource", "equals": ["bedrock-agent-runtime.amazonaws.com"]}
            ]}
        ]})
        assert s == Status.COMPLIANT


# ─── 9. bedrock_model_access_tag_required ────────────────────────────────────

class TestBedrockModelAccessTagRequired:
    def _r(self, tags, params=None):
        cfg = {"provisionedModelArn": "arn:aws:bedrock:us-west-2:123:provisioned-model/my-model"}
        return _eval(MOD["tag"], cfg, RT["prov"], tags=tags, rule_params=params)

    def test_all_tags_present(self):
        s, _ = self._r({"DataClassification": "Confidential", "Team": "ml", "Environment": "prod"})
        assert s == Status.COMPLIANT

    def test_missing_one_tag(self):
        s, ann = self._r({"DataClassification": "Confidential", "Team": "ml"})
        assert s == Status.NON_COMPLIANT
        assert "Environment" in ann

    def test_all_tags_missing(self):
        s, ann = self._r({})
        assert s == Status.NON_COMPLIANT
        for t in ["DataClassification", "Team", "Environment"]:
            assert t in ann

    def test_empty_tag_value_counts_as_missing(self):
        s, ann = self._r({"DataClassification": "", "Team": "ml", "Environment": "prod"})
        assert s == Status.NON_COMPLIANT
        assert "DataClassification" in ann

    def test_custom_required_tags(self):
        s, _ = self._r(
            {"Owner": "alice", "CostCenter": "123"},
            params={"required_tags": "Owner,CostCenter"},
        )
        assert s == Status.COMPLIANT

    def test_custom_required_tags_missing(self):
        s, ann = self._r({}, params={"required_tags": "Owner,CostCenter"})
        assert s == Status.NON_COMPLIANT
        assert "Owner" in ann


# ─── 10. mcp_server_auth_required ────────────────────────────────────────────

class TestMcpServerAuthRequired:
    def _r(self, cfg, tags=None, params=None, supplementary=None):
        return _eval(MOD["mcp"], cfg, RT["fn"], tags=tags, rule_params=params, supplementary=supplementary)

    def test_not_mcp_server_skipped(self):
        s, _ = self._r({"functionName": "other-fn"}, tags={})
        assert s == Status.NOT_APPLICABLE

    def test_oauth_env_var(self):
        cfg = {"functionName": "mcp-fn", "environment": {"variables": {"MCP_AUTH_TYPE": "oauth"}}}
        s, _ = self._r(cfg, tags={"MCPServer": "true"})
        assert s == Status.COMPLIANT

    def test_sigv4_env_var(self):
        cfg = {"functionName": "mcp-fn", "environment": {"variables": {"MCP_AUTH_TYPE": "sigv4"}}}
        s, _ = self._r(cfg, tags={"MCPServer": "true"})
        assert s == Status.COMPLIANT

    def test_function_url_iam_auth(self):
        import json
        cfg = {"functionName": "mcp-fn", "environment": {"variables": {}}}
        supp = {"FunctionUrlConfig": json.dumps({"AuthType": "AWS_IAM"})}
        s, _ = self._r(cfg, tags={"MCPServer": "true"}, supplementary=supp)
        assert s == Status.COMPLIANT

    def test_no_auth(self):
        cfg = {"functionName": "mcp-fn", "environment": {"variables": {}}}
        s, ann = self._r(cfg, tags={"MCPServer": "true"})
        assert s == Status.NON_COMPLIANT
        assert "MCP_AUTH_TYPE" in ann

    def test_function_url_none_auth(self):
        import json
        cfg = {"functionName": "mcp-fn", "environment": {"variables": {}}}
        supp = {"FunctionUrlConfig": json.dumps({"AuthType": "NONE"})}
        s, _ = self._r(cfg, tags={"MCPServer": "true"}, supplementary=supp)
        assert s == Status.NON_COMPLIANT

    def test_wrong_tag_value_skipped(self):
        cfg = {"functionName": "other-fn", "environment": {"variables": {}}}
        s, _ = self._r(cfg, tags={"MCPServer": "false"}, params={"tag_key": "MCPServer", "tag_value": "true"})
        assert s == Status.NOT_APPLICABLE

    def test_oauth2_env_var(self):
        cfg = {"functionName": "mcp-fn", "environment": {"variables": {"MCP_AUTH_TYPE": "oauth2"}}}
        s, _ = self._r(cfg, tags={"MCPServer": "true"})
        assert s == Status.COMPLIANT
