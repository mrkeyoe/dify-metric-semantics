"""dify-metric-semantics — metric definition registry and arbitration core.

The ``core`` package is standard-library only. The Dify plugin adapter
(``main.py``, ``provider/``, ``tools/``) imports it lazily, so unit tests run
without the ``dify_plugin`` SDK installed.
"""

from __future__ import annotations

from .arbitration import ArbitrationResult, arbitrate_candidates
from .config import Config
from .conflict import conflicting_ids, detect_conflicts, summarize_report
from .errors import (
    ArbitrationParseError,
    DuplicateMetricError,
    LLMError,
    LLMUnavailable,
    MetricNotFoundError,
    MetricSemanticsError,
    StorageError,
    ValidationError,
)
from .factory import build_llm, build_service
from .feedback import ema_update, signal_from_outcome
from .llm import HttpChatClient, LLMClient, StaticLLMClient
from .matching import match_definitions, suggest_definitions
from .models import (
    Candidate,
    ConflictReport,
    FeedbackResult,
    MetricDefinition,
    PairConflict,
    ResolveResult,
    make_metric_id,
)
from .prompts import PROMPT_VERSION, build_arbitration_prompt, parse_arbitration_response
from .seeds import example_definitions
from .service import MetricSemanticsService
from .storage import InMemoryMetricStore, JsonMetricStore, open_store
from .tool_handlers import TOOL_HANDLERS, dispatch

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ArbitrationParseError",
    "ArbitrationResult",
    "Candidate",
    "Config",
    "ConflictReport",
    "DuplicateMetricError",
    "FeedbackResult",
    "HttpChatClient",
    "InMemoryMetricStore",
    "JsonMetricStore",
    "LLMClient",
    "LLMError",
    "LLMUnavailable",
    "MetricDefinition",
    "MetricNotFoundError",
    "MetricSemanticsError",
    "MetricSemanticsService",
    "PROMPT_VERSION",
    "PairConflict",
    "ResolveResult",
    "StaticLLMClient",
    "StorageError",
    "TOOL_HANDLERS",
    "ValidationError",
    "arbitrate_candidates",
    "build_arbitration_prompt",
    "build_llm",
    "build_service",
    "conflicting_ids",
    "detect_conflicts",
    "dispatch",
    "ema_update",
    "example_definitions",
    "make_metric_id",
    "match_definitions",
    "open_store",
    "parse_arbitration_response",
    "signal_from_outcome",
    "suggest_definitions",
    "summarize_report",
]