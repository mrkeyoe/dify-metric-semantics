"""Exponential-moving-average confidence updates (deliberately not ML)."""

from __future__ import annotations

from .errors import ValidationError
from .models import clamp_confidence

SIGNAL_SUCCESS = 1.0
SIGNAL_FAILURE = 0.0


def signal_from_outcome(success: bool) -> float:
    return SIGNAL_SUCCESS if success else SIGNAL_FAILURE


def ema_update(current: float, signal: float, alpha: float) -> float:
    """``new = (1 - alpha) * current + alpha * signal``, then clamp to (0, 1).

    ``alpha`` is the weight of the newest observation. With the default 0.2 a
    single failure moves a 0.60 definition to 0.48, and it takes several
    consistent successes to climb back — which is the intended inertia.
    """

    try:
        current_value = float(current)
        signal_value = float(signal)
        alpha_value = float(alpha)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"EMA inputs must be numbers: {(current, signal, alpha)!r}") from exc

    if not 0.0 < alpha_value <= 1.0:
        raise ValidationError(f"alpha must be within (0, 1], got {alpha_value}")
    if not 0.0 <= signal_value <= 1.0:
        raise ValidationError(f"signal must be within [0, 1], got {signal_value}")
    if not 0.0 <= current_value <= 1.0:
        raise ValidationError(f"current confidence must be within [0, 1], got {current_value}")

    updated = (1.0 - alpha_value) * current_value + alpha_value * signal_value
    return clamp_confidence(updated)