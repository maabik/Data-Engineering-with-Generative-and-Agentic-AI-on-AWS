"""AWS service collectors. Each collect_* fn returns a dict slice of the AccountSnapshot.

Collectors are read-only — only List*/Get*/Describe* APIs. On AccessDenied they
record `error: "..."` and return partial data so the assessment can still score
dimensions whose data did come back.
"""
from .s3 import collect_s3
from .glue import collect_glue
from .lakeformation import collect_lakeformation
from .s3tables import collect_s3tables
from .kms import collect_kms
from .athena import collect_athena
from .bedrock_kb import collect_bedrock_kb
from .s3_vectors import collect_s3_vectors
from .opensearch import collect_opensearch
from .datazone import collect_datazone
from .agentcore import collect_agentcore
from .cost_explorer import collect_cost_explorer
from .cloudtrail import collect_cloudtrail

__all__ = [
    "collect_s3",
    "collect_glue",
    "collect_lakeformation",
    "collect_s3tables",
    "collect_kms",
    "collect_athena",
    "collect_bedrock_kb",
    "collect_s3_vectors",
    "collect_opensearch",
    "collect_datazone",
    "collect_agentcore",
    "collect_cost_explorer",
    "collect_cloudtrail",
]
