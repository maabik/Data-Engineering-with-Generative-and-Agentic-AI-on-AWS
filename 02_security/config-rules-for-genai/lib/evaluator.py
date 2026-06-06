"""Core evaluator helpers shared by every Config rule handler.

Usage in a rule handler:
    from lib import Evaluator, Status

    def evaluate(config_item, rule_params):
        if <compliant condition>:
            return Status.COMPLIANT
        return Status.NON_COMPLIANT, "reason string"

    def lambda_handler(event, context):
        return Evaluator(event, context).run(evaluate)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# AWS Config only accepts these three compliance values.
class Status:
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class Evaluation:
    resource_type: str
    resource_id: str
    compliance_type: str
    annotation: str = ""
    ordering_timestamp: str = ""


def build_event(
    config_item: dict[str, Any],
    rule_params: dict[str, Any] | None = None,
    result_token: str = "TESTTOKEN",
    event_left_scope: bool = False,
) -> dict[str, Any]:
    """Build a synthetic AWS Config event for unit testing rule handlers."""
    return {
        "invokingEvent": json.dumps({
            "configurationItem": config_item,
            "messageType": "ConfigurationItemChangeNotification",
        }),
        "ruleParameters": json.dumps(rule_params or {}),
        "resultToken": result_token,
        "eventLeftScope": event_left_scope,
    }


class Evaluator:
    """Parses the Lambda event, runs the evaluate function, calls put_evaluations."""

    def __init__(self, event: dict[str, Any], context: Any):
        self.event = event
        self.context = context
        self.invoking_event: dict[str, Any] = json.loads(event.get("invokingEvent", "{}"))
        raw_params = event.get("ruleParameters", "{}")
        self.rule_params: dict[str, Any] = json.loads(raw_params) if raw_params else {}
        self.result_token: str = event.get("resultToken", "TESTTOKEN")

    @property
    def config_item(self) -> dict[str, Any] | None:
        return self.invoking_event.get("configurationItem")

    @property
    def message_type(self) -> str:
        return self.invoking_event.get("messageType", "")

    def run(
        self,
        evaluate_fn: Callable[[dict[str, Any], dict[str, Any]], Any],
        *,
        applicable_resource_types: Sequence[str] | None = None,
        config_client=None,
    ) -> list[dict[str, Any]]:
        """Execute evaluate_fn and submit results to AWS Config.

        evaluate_fn signature:
            (config_item, rule_params) -> Status string
                                       | (Status string, annotation)
                                       | Evaluation
                                       | list[Evaluation]

        Returns the list of evaluations sent (useful for testing without a
        real Config client).
        """
        # Scheduled rules (OversizedConfigurationItemChangeNotification or
        # ScheduledNotification) should be handled separately; skip here.
        if self.message_type not in (
            "ConfigurationItemChangeNotification",
            "OversizedConfigurationItemChangeNotification",
        ):
            log.info("Non-change message type %s — skipping", self.message_type)
            return []

        ci = self.config_item
        if ci is None:
            log.warning("No configurationItem in event — skipping")
            return []

        # If the resource type isn't one we care about, skip it.
        if applicable_resource_types:
            if ci.get("resourceType") not in applicable_resource_types:
                evaluations = [_not_applicable(ci, "Resource type not in scope")]
                _submit(evaluations, self.result_token, config_client)
                return [_eval_to_dict(e) for e in evaluations]

        # Resource deleted — mark NOT_APPLICABLE.
        if ci.get("configurationItemStatus") in ("ResourceDeleted", "ResourceNotRecorded"):
            evaluations = [_not_applicable(ci, "Resource deleted or not recorded")]
            _submit(evaluations, self.result_token, config_client)
            return [_eval_to_dict(e) for e in evaluations]

        result = evaluate_fn(ci, self.rule_params)
        evaluations = _coerce_evaluations(result, ci)
        _submit(evaluations, self.result_token, config_client)
        return [_eval_to_dict(e) for e in evaluations]


def _coerce_evaluations(
    result: Any,
    ci: dict[str, Any],
) -> list[Evaluation]:
    if isinstance(result, list):
        return result
    if isinstance(result, Evaluation):
        return [result]
    if isinstance(result, tuple):
        status, annotation = result[0], result[1] if len(result) > 1 else ""
    else:
        status, annotation = result, ""
    return [Evaluation(
        resource_type=ci.get("resourceType", "AWS::Config::ResourceCompliance"),
        resource_id=ci.get("resourceId", "unknown"),
        compliance_type=status,
        annotation=str(annotation)[:256],
        ordering_timestamp=ci.get("configurationItemCaptureTime", ""),
    )]


def _not_applicable(ci: dict[str, Any], reason: str) -> Evaluation:
    return Evaluation(
        resource_type=ci.get("resourceType", "AWS::Config::ResourceCompliance"),
        resource_id=ci.get("resourceId", "unknown"),
        compliance_type=Status.NOT_APPLICABLE,
        annotation=reason,
        ordering_timestamp=ci.get("configurationItemCaptureTime", ""),
    )


def _eval_to_dict(e: Evaluation) -> dict[str, Any]:
    d: dict[str, Any] = {
        "ComplianceResourceType": e.resource_type,
        "ComplianceResourceId": e.resource_id,
        "ComplianceType": e.compliance_type,
        "OrderingTimestamp": e.ordering_timestamp,
    }
    if e.annotation:
        d["Annotation"] = e.annotation
    return d


def _submit(
    evaluations: list[Evaluation],
    result_token: str,
    config_client=None,
) -> None:
    if not evaluations:
        return
    if config_client is None:
        import boto3
        config_client = boto3.client("config")
    dicts = [_eval_to_dict(e) for e in evaluations]
    # put_evaluations accepts max 100 per call.
    for i in range(0, len(dicts), 100):
        config_client.put_evaluations(
            Evaluations=dicts[i : i + 100],
            ResultToken=result_token,
        )
