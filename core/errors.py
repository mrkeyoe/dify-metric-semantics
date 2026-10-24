"""Exception hierarchy for the metric semantics layer.

All exceptions are plain stdlib so that ``core`` stays importable without the
Dify plugin SDK.
"""

from __future__ import annotations


class MetricSemanticsError(Exception):
    """Base class for every error raised by this project."""


class ValidationError(MetricSemanticsError, ValueError):
    """A metric definition, tool parameter or arbitration payload is invalid."""


class DuplicateMetricError(MetricSemanticsError):
    """A metric with the same id already exists and ``replace`` was not set."""


class MetricNotFoundError(MetricSemanticsError, KeyError):
    """No metric definition is registered under the requested id."""

    def __str__(self) -> str:  # KeyError repr adds quotes, which is confusing.
        return str(self.args[0]) if self.args else "metric not found"


class StorageError(MetricSemanticsError):
    """The persistence layer failed (I/O error, lock timeout, corrupt file)."""


class LLMError(MetricSemanticsError):
    """A model call failed or returned something unusable."""


class LLMUnavailable(LLMError):
    """No usable model gateway is configured in the current environment."""


class ArbitrationParseError(MetricSemanticsError):
    """The model answer could not be parsed into the expected JSON contract."""