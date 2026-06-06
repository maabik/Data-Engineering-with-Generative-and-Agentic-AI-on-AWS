"""Tests for lib/evaluator.py shared infrastructure."""

import json
import pytest
from lib.evaluator import Evaluator, Status, Evaluation, build_event, _coerce_evaluations
from tests.helpers import FakeConfigClient, make_event


CI_TEMPLATE = {
    "resourceType": "AWS::KMS::Key",
    "resourceId": "key-abc123",
    "configurationItemStatus": "OK",
    "configurationItemCaptureTime": "2026-01-01T00:00:00Z",
    "tags": {},
}


class TestStatus:
    def test_constants(self):
        assert Status.COMPLIANT == "COMPLIANT"
        assert Status.NON_COMPLIANT == "NON_COMPLIANT"
        assert Status.NOT_APPLICABLE == "NOT_APPLICABLE"


class TestBuildEvent:
    def test_round_trips_config_item(self):
        ci = {**CI_TEMPLATE}
        event = build_event(ci)
        parsed = json.loads(event["invokingEvent"])
        assert parsed["configurationItem"]["resourceId"] == "key-abc123"

    def test_result_token(self):
        event = build_event(CI_TEMPLATE, result_token="MY_TOKEN")
        assert event["resultToken"] == "MY_TOKEN"

    def test_rule_params(self):
        event = build_event(CI_TEMPLATE, rule_params={"foo": "bar"})
        assert json.loads(event["ruleParameters"]) == {"foo": "bar"}


class TestCoerceEvaluations:
    def test_string_status(self):
        ci = {**CI_TEMPLATE}
        evals = _coerce_evaluations(Status.COMPLIANT, ci)
        assert len(evals) == 1
        assert evals[0].compliance_type == Status.COMPLIANT

    def test_tuple_with_annotation(self):
        ci = {**CI_TEMPLATE}
        evals = _coerce_evaluations((Status.NON_COMPLIANT, "bad thing"), ci)
        assert evals[0].compliance_type == Status.NON_COMPLIANT
        assert evals[0].annotation == "bad thing"

    def test_annotation_truncated_at_256(self):
        ci = {**CI_TEMPLATE}
        evals = _coerce_evaluations((Status.NON_COMPLIANT, "x" * 300), ci)
        assert len(evals[0].annotation) <= 256

    def test_evaluation_object(self):
        ci = {**CI_TEMPLATE}
        e = Evaluation("AWS::KMS::Key", "key-1", Status.COMPLIANT, "ok", "2026-01-01T00:00:00Z")
        evals = _coerce_evaluations(e, ci)
        assert evals == [e]

    def test_list_of_evaluations(self):
        ci = {**CI_TEMPLATE}
        evals_in = [
            Evaluation("AWS::KMS::Key", "k1", Status.COMPLIANT, "", ""),
            Evaluation("AWS::KMS::Key", "k2", Status.NON_COMPLIANT, "bad", ""),
        ]
        evals_out = _coerce_evaluations(evals_in, ci)
        assert evals_out is evals_in


class TestEvaluator:
    def _run(self, event, fn, applicable_types=None):
        fake = FakeConfigClient()
        ev = Evaluator(event, None)
        return ev.run(fn, applicable_resource_types=applicable_types, config_client=fake), fake

    def test_compliant(self):
        event = make_event("AWS::KMS::Key", "k1", {})
        result, fake = self._run(event, lambda ci, p: Status.COMPLIANT)
        assert fake.compliance_types() == ["COMPLIANT"]

    def test_non_compliant(self):
        event = make_event("AWS::KMS::Key", "k1", {})
        result, fake = self._run(event, lambda ci, p: (Status.NON_COMPLIANT, "bad"))
        assert fake.compliance_types() == ["NON_COMPLIANT"]

    def test_wrong_resource_type_is_not_applicable(self):
        event = make_event("AWS::S3::Bucket", "bucket1", {})
        result, fake = self._run(
            event,
            lambda ci, p: Status.COMPLIANT,
            applicable_types=["AWS::KMS::Key"],
        )
        assert fake.compliance_types() == ["NOT_APPLICABLE"]

    def test_deleted_resource_is_not_applicable(self):
        event = make_event("AWS::KMS::Key", "k1", {}, status="ResourceDeleted")
        result, fake = self._run(event, lambda ci, p: Status.COMPLIANT)
        assert fake.compliance_types() == ["NOT_APPLICABLE"]

    def test_non_change_message_skipped(self):
        event = make_event("AWS::KMS::Key", "k1", {}, message_type="ScheduledNotification")
        result, fake = self._run(event, lambda ci, p: Status.COMPLIANT)
        assert fake.calls == []
        assert result == []

    def test_puts_100_per_batch(self):
        """Evaluations are batched at 100 per put_evaluations call."""
        fake = FakeConfigClient()
        # Build an evaluate function that returns 150 evaluations
        many = [Evaluation("AWS::KMS::Key", f"k{i}", Status.COMPLIANT, "", "") for i in range(150)]
        event = make_event("AWS::KMS::Key", "k0", {})
        ev = Evaluator(event, None)
        ev.run(lambda ci, p: many, config_client=fake)
        assert len(fake.calls) == 2
        assert len(fake.calls[0]) == 100
        assert len(fake.calls[1]) == 50

    def test_result_token_passed(self):
        captured = {}
        class CapturingClient:
            def put_evaluations(self, Evaluations, ResultToken):
                captured["token"] = ResultToken
        event = make_event("AWS::KMS::Key", "k1", {})
        event["resultToken"] = "MY_SPECIAL_TOKEN"
        Evaluator(event, None).run(lambda ci, p: Status.COMPLIANT, config_client=CapturingClient())
        assert captured["token"] == "MY_SPECIAL_TOKEN"
