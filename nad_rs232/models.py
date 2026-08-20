"""Supported amplifier models for nad_rs232.

The NAD C338, C368 and C388 all speak the same ASCII RS-232 protocol
(v2.x). The differences relevant to this library are the volume range
and the number of selectable sources (which also depends on installed
MDC cards and is therefore queried from the device at runtime via
``Main.Sources?`` whenever possible).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AmplifierModel:
    """Static description of an amplifier model."""

    key: str
    name: str
    # Hardware volume range in dB. The official C368 command list documents
    # -70..+10, but real-world measurements on a C368 show the amplifier
    # accepts and reports up to +12 dB (which its front panel maps to 100%).
    min_volume_db: float
    max_volume_db: float
    # Fallback source count used when Main.Sources? gets no answer.
    default_source_count: int


MODELS: dict[str, AmplifierModel] = {
    "c338": AmplifierModel(
        key="c338",
        name="NAD C338",
        min_volume_db=-70.0,
        max_volume_db=12.0,
        default_source_count=10,
    ),
    "c368": AmplifierModel(
        key="c368",
        name="NAD C368",
        min_volume_db=-70.0,
        max_volume_db=12.0,
        default_source_count=10,
    ),
    "c388": AmplifierModel(
        key="c388",
        name="NAD C388",
        min_volume_db=-70.0,
        max_volume_db=12.0,
        default_source_count=10,
    ),
    "other": AmplifierModel(
        key="other",
        name="Other",
        min_volume_db=-70.0,
        max_volume_db=12.0,
        default_source_count=10,
    ),
}
