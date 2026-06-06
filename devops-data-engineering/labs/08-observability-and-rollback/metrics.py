"""Helper for emitting CloudWatch metrics with deploy_id always attached.

    from metrics import put_metric
    put_metric("DQRowsScanned", value=12345, deploy_id="v1.4.2")
"""
from __future__ import annotations

import os
from typing import Optional

import boto3

_NAMESPACE = "DataPipeline/Reference"


def put_metric(
    name: str,
    value: float,
    *,
    unit: str = "Count",
    deploy_id: Optional[str] = None,
    extra_dims: Optional[dict[str, str]] = None,
) -> None:
    cw = boto3.client("cloudwatch")
    dims = [{"Name": "DeployId", "Value": deploy_id or os.environ.get("DEPLOY_ID", "unknown")}]
    for k, v in (extra_dims or {}).items():
        dims.append({"Name": k, "Value": v})
    cw.put_metric_data(
        Namespace=_NAMESPACE,
        MetricData=[
            {"MetricName": name, "Value": value, "Unit": unit, "Dimensions": dims},
        ],
    )
