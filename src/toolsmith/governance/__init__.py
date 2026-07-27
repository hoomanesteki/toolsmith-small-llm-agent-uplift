"""Data governance: provenance, the license firewall, and the lineage DAG.

This package is the part of the project that reads senior. Every dataset row
knows where it came from, every published number knows which inputs produced
it, and a CI job fails the build if a model whose terms forbid it ever appears
on a training row.
"""

from toolsmith.governance.firewall import (
    FirewallReport,
    Violation,
    allowed_generators,
    scan,
)
from toolsmith.governance.lineage import LineageLog, LineageNode
from toolsmith.governance.provenance import Provenance, content_hash, oracle_provenance

__all__ = [
    "FirewallReport",
    "LineageLog",
    "LineageNode",
    "Provenance",
    "Violation",
    "allowed_generators",
    "content_hash",
    "oracle_provenance",
    "scan",
]
