"""Structured-logging helper for Glue jobs.

Every emitted line is a JSON object with deploy_id and trace_id, ready
for CloudWatch Logs Insights. Wire it from a Glue job's main():

    from awsglue.utils import getResolvedOptions
    args = getResolvedOptions(sys.argv, ["deploy_id"])
    log = get_logger(__name__, args)
    log.info("starting", extra={"step": "ingest"})
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


class _JsonFormatter(logging.Formatter):
    def __init__(self, deploy_id: str, service: str, trace_id: str) -> None:
        super().__init__()
        self._deploy_id = deploy_id
        self._service = service
        self._trace_id = trace_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "service": self._service,
            "deploy_id": self._deploy_id,
            "trace_id": self._trace_id,
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in {"args", "asctime", "created", "exc_info", "exc_text", "filename",
                     "funcName", "levelname", "levelno", "lineno", "message", "module",
                     "msecs", "msg", "name", "pathname", "process", "processName",
                     "relativeCreated", "stack_info", "thread", "threadName"}:
                continue
            payload[k] = v
        return json.dumps(payload)


def get_logger(name: str, args: dict[str, str]) -> logging.Logger:
    deploy_id = args.get("deploy_id") or os.environ.get("DEPLOY_ID", "unknown")
    trace_id = args.get("trace_id") or os.environ.get("AWS_GLUE_JOB_RUN_ID", "unknown")

    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_JsonFormatter(deploy_id=deploy_id, service=name, trace_id=trace_id))
        log.addHandler(h)
        log.propagate = False
    return log
