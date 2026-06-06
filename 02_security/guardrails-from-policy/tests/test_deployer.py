"""Tests for the deployer — all AWS calls are mocked."""

import io
import json

import pytest

from guardrails_from_policy.deployer import deploy_guardrail, DeployResult
from guardrails_from_policy.guardrail_builder import build_guardrail_config
from guardrails_from_policy.policy_parser import _coerce
from tests.fixtures import SAMPLE_PARSED_JSON


def _config():
    return build_guardrail_config(_coerce(SAMPLE_PARSED_JSON))


class TestDryRun:
    def test_dry_run_returns_fake_result(self, capsys):
        result = deploy_guardrail(_config(), dry_run=True)
        assert isinstance(result, DeployResult)
        assert result.guardrail_id == "dry-run-id"
        assert result.version == "DRAFT"
        assert result.created is True

    def test_dry_run_prints_config(self, capsys):
        deploy_guardrail(_config(), dry_run=True)
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "name" in out


class TestDeployCreate:
    def _make_mock_bedrock(self, existing_guardrails=None):
        """Return a fake boto3 'bedrock' client that simulates create/list flows."""
        class FakePageIterator:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

        class FakePaginator:
            def __init__(self, pages):
                self._pages = pages

            def paginate(self):
                return FakePageIterator(self._pages)

        class FakeClient:
            def __init__(self, guardrails):
                self._guardrails = guardrails or []
                self.create_called = False
                self.update_called = False
                self.version_called = False

            def get_paginator(self, op):
                assert op == "list_guardrails"
                return FakePaginator([{"guardrails": self._guardrails}])

            def create_guardrail(self, **kwargs):
                self.create_called = True
                self.create_kwargs = kwargs
                return {"guardrailId": "new-gid-123"}

            def update_guardrail(self, **kwargs):
                self.update_called = True
                self.update_kwargs = kwargs

            def create_guardrail_version(self, **kwargs):
                self.version_called = True
                return {"version": "1"}

            def get_guardrail(self, **kwargs):
                return {"guardrailArn": "arn:aws:bedrock:us-west-2:000000000000:guardrail/new-gid-123"}

        return FakeClient(existing_guardrails)

    def _patch_boto3(self, monkeypatch, fake_client):
        import boto3
        monkeypatch.setattr(boto3, "client", lambda *a, **kw: fake_client)

    def test_create_when_no_existing(self, monkeypatch):
        fake_client = self._make_mock_bedrock()
        self._patch_boto3(monkeypatch, fake_client)

        result = deploy_guardrail(_config())
        assert fake_client.create_called
        assert not fake_client.update_called
        assert fake_client.version_called
        assert result.created is True
        assert result.guardrail_id == "new-gid-123"
        assert result.version == "1"

    def test_update_when_existing(self, monkeypatch):
        existing = [{"guardrailId": "existing-gid", "name": "acme-data-classification-policy"}]
        fake_client = self._make_mock_bedrock(existing)
        self._patch_boto3(monkeypatch, fake_client)

        result = deploy_guardrail(_config())
        assert fake_client.update_called
        assert not fake_client.create_called
        assert fake_client.version_called
        assert result.created is False
        assert result.guardrail_id == "existing-gid"

    def test_kms_key_passed_to_create(self, monkeypatch):
        fake_client = self._make_mock_bedrock()
        self._patch_boto3(monkeypatch, fake_client)

        deploy_guardrail(_config(), kms_key_id="arn:aws:kms:::key/abc")
        assert fake_client.create_kwargs.get("kmsKeyId") == "arn:aws:kms:::key/abc"

    def test_tags_passed_to_create(self, monkeypatch):
        fake_client = self._make_mock_bedrock()
        self._patch_boto3(monkeypatch, fake_client)

        deploy_guardrail(_config(), tags={"Env": "test", "Team": "security"})
        tags = {t["key"]: t["value"] for t in fake_client.create_kwargs.get("tags", [])}
        assert tags == {"Env": "test", "Team": "security"}

    def test_result_has_arn(self, monkeypatch):
        fake_client = self._make_mock_bedrock()
        self._patch_boto3(monkeypatch, fake_client)

        result = deploy_guardrail(_config())
        assert result.guardrail_arn.startswith("arn:aws:bedrock:")

    def test_update_does_not_pass_tags_or_kms(self, monkeypatch):
        existing = [{"guardrailId": "existing-gid", "name": "acme-data-classification-policy"}]
        fake_client = self._make_mock_bedrock(existing)
        self._patch_boto3(monkeypatch, fake_client)

        deploy_guardrail(_config(), kms_key_id="arn:...", tags={"k": "v"})
        assert "tags" not in fake_client.update_kwargs
        assert "kmsKeyId" not in fake_client.update_kwargs
