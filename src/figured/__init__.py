"""figured: show your work.

Verify that every number in an LLM-generated answer traces to the rows it was derived from,
directly or as a sum, difference, ratio, or percentage of them.

    >>> from figured import trace
    >>> report = trace("California has 39.3 million people.", [{"state": "CA", "pop": 39346023}])
    >>> report.ok
    True
"""

from figured.core import trace
from figured.evidence import build_evidence
from figured.extract import Figure, extract_numbers
from figured.policy import DERIVATIONS, LENIENT, STRICT, Policy
from figured.report import Report, Result

__version__ = "0.4.1"
__all__ = [
    "DERIVATIONS",
    "LENIENT",
    "STRICT",
    "Figure",
    "Policy",
    "Report",
    "Result",
    "__version__",
    "build_evidence",
    "extract_numbers",
    "trace",
]
