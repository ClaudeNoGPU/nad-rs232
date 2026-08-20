"""Async library to control NAD amplifiers (C338/C368/C388) over RS-232.

Built on serialx, so the same code works over a local serial port
(``/dev/ttyUSB0``) or an ESPHome serial proxy, transparently.
"""

from .amplifier import NADAmplifier, StateCallback
from .const import (
    BAUD_RATE,
    COMMAND_TIMEOUT,
    PROBE_TIMEOUT,
    STARTUP_PROTECTION_WINDOW,
    ListeningMode,
    PowerState,
    VolumeDisplayMode,
)
from .models import MODELS, AmplifierModel
from .protocol import (
    MAX_VOLUME_DB,
    MIN_VOLUME_DB,
    VOLUME_CURVE,
    db_to_percent,
    percent_to_db,
    round_half_db,
)
from .state import AmplifierState, SourceInfo

__version__ = "0.1.0"

__all__ = [
    "BAUD_RATE",
    "COMMAND_TIMEOUT",
    "MAX_VOLUME_DB",
    "MIN_VOLUME_DB",
    "MODELS",
    "PROBE_TIMEOUT",
    "STARTUP_PROTECTION_WINDOW",
    "VOLUME_CURVE",
    "AmplifierModel",
    "AmplifierState",
    "ListeningMode",
    "NADAmplifier",
    "PowerState",
    "SourceInfo",
    "StateCallback",
    "VolumeDisplayMode",
    "db_to_percent",
    "percent_to_db",
    "round_half_db",
]
