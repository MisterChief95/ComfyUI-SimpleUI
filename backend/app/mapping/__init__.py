"""Runtime workflow mapping: import, classify, present, submit.

The graph is authoritative execution data; everything this package produces is
a separate presentation layer over it (docs/WORKFLOW_MAPPING.md). No function
here rewrites topology, literal types or omitted optionals.
"""

from .controls import build_control_schema
from .corrections import (
    Correction,
    CorrectionError,
    Presentation,
    SaveCorrection,
    apply_corrections,
    content_hash,
    structural_signature,
)
from .importer import (
    GraphImportError,
    Link,
    classify_input,
    looks_like_link,
    parse_graph,
)
from .submission import (
    SeedPolicy,
    SubmissionError,
    build_submission_graph,
    resolve_seed,
)

__all__ = [
    "Correction",
    "CorrectionError",
    "GraphImportError",
    "Link",
    "Presentation",
    "SaveCorrection",
    "SeedPolicy",
    "SubmissionError",
    "apply_corrections",
    "build_control_schema",
    "build_submission_graph",
    "classify_input",
    "content_hash",
    "looks_like_link",
    "parse_graph",
    "resolve_seed",
    "structural_signature",
]
