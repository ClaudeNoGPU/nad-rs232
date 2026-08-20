"""Protocol helpers for nad_rs232.

The NAD RS-232 protocol v2.x is ASCII based. Every message has the form::

    <Prefix>.<Variable><Operator><Value><CR>

For example: ``Main.Volume=-25.5\\r``. Queries use the ``?`` operator and
are always answered with ``=``. Any data that does not follow this format
must be ignored.

This module also contains the dB <-> percent conversion curve measured on
a real NAD C368: the amplifier's own front panel percentage scale is *not*
linear in dB, so a piecewise-linear interpolation over measured breakpoints
is used to match the device display exactly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

# (dB, percent) breakpoints measured on a real NAD C368. The front panel
# shows 0% at -70 dB and 100% at +12 dB, with a non-linear mapping in
# between. Linear interpolation between consecutive breakpoints reproduces
# the panel value within rounding error.
VOLUME_CURVE: tuple[tuple[float, float], ...] = (
    (-70.0, 0.0),
    (-60.0, 16.0),
    (-40.0, 35.0),
    (-35.0, 40.0),
    (-30.0, 45.0),
    (-25.5, 50.0),
    (-15.0, 60.0),
    (-10.0, 67.0),
    (-5.0, 73.0),
    (0.0, 80.0),
    (5.0, 88.0),
    (12.0, 100.0),
)

MIN_VOLUME_DB = VOLUME_CURVE[0][0]
MAX_VOLUME_DB = VOLUME_CURVE[-1][0]


def round_half_db(db: float) -> float:
    """Round a dB value to the nearest 0.5 dB (the amplifier's step size)."""
    return round(db * 2.0) / 2.0


def db_to_percent(db: float) -> float:
    """Convert a volume in dB to the amplifier's front panel percentage."""
    if db <= MIN_VOLUME_DB:
        return 0.0
    if db >= MAX_VOLUME_DB:
        return 100.0

    for (db_lo, pct_lo), (db_hi, pct_hi) in zip(VOLUME_CURVE, VOLUME_CURVE[1:]):
        if db <= db_hi:
            ratio = (db - db_lo) / (db_hi - db_lo)
            return pct_lo + ratio * (pct_hi - pct_lo)

    return 100.0  # pragma: no cover - unreachable


def percent_to_db(percent: float) -> float:
    """Convert a front panel percentage to dB, rounded to 0.5 dB."""
    if percent <= 0.0:
        return MIN_VOLUME_DB
    if percent >= 100.0:
        return MAX_VOLUME_DB

    for (db_lo, pct_lo), (db_hi, pct_hi) in zip(VOLUME_CURVE, VOLUME_CURVE[1:]):
        if percent <= pct_hi:
            ratio = (percent - pct_lo) / (pct_hi - pct_lo)
            return round_half_db(db_lo + ratio * (db_hi - db_lo))

    return MAX_VOLUME_DB  # pragma: no cover - unreachable


def format_volume_db(db: float) -> str:
    """Format a dB value for the Main.Volume command.

    The amplifier accepts values with one decimal (0.5 dB steps).
    """
    return f"{round_half_db(db):.1f}"


def parse_message(message: str) -> tuple[str, str] | None:
    """Split a raw protocol line into (variable, value).

    ``Main.Volume=-25.5`` -> ``("Main.Volume", "-25.5")``.
    Returns ``None`` for anything that does not follow the protocol format
    (the protocol mandates such data be ignored).
    """
    variable, sep, value = message.partition("=")
    if not sep or not variable or "." not in variable:
        return None
    return variable.strip(), value.strip()


def parse_volume_value(
    value: str, *, percent_mode: bool = False
) -> float | None:
    """Parse a Main.Volume value into dB, with validation.

    The format is determined *solely* by the content of the value: if it
    ends with ``%`` it is a percentage, otherwise it is dB. The amplifier's
    declared VolumeDisplayMode is deliberately NOT trusted here: a real
    NAD C368 running in Percent display mode still answers ``Main.Volume?``
    with a dB value (e.g. ``-32.5``), so relying on the declared mode would
    wrongly reject that value. ``percent_mode`` is kept only for backwards
    compatibility of the signature and is ignored.

    Corrupted UART frames occasionally produce garbage such as ``100208``
    which must be rejected.

    Returns the volume in dB, or ``None`` if the value is invalid.
    """
    raw = value.strip()

    if raw.endswith("%"):
        raw = raw[:-1].strip()
        try:
            number = float(raw)
        except ValueError:
            return None
        if not 0.0 <= number <= 100.0:
            return None
        return percent_to_db(number)

    # No percent sign: the value is in dB.
    try:
        number = float(raw)
    except ValueError:
        return None
    if not MIN_VOLUME_DB <= number <= MAX_VOLUME_DB:
        return None
    return round_half_db(number)


@dataclass
class PendingQuery:
    """A pending query waiting for a response with a matching variable."""

    variable: str
    future: asyncio.Future[str]
