"""Constants for nad_rs232."""

from __future__ import annotations

from enum import Enum

# NAD RS-232 protocol v2.x: 115200 bps, 8 data bits, 1 stop bit, no parity,
# no flow control. All communication is ASCII.
BAUD_RATE = 115200

# Message terminator. The protocol states a <CR> (and/or <LF>) terminates
# every message. We also *prepend* a <CR> to every command we send, as
# recommended by the protocol documentation, to flush any noise that may
# have been received by the amplifier.
CR = b"\r"
LF = b"\n"

# How long to wait for a reply to a direct query before timing out.
COMMAND_TIMEOUT = 2.0

# How long to wait for the reply when probing the device during the
# initial connection (Main.Model?).
PROBE_TIMEOUT = 3.0

# Small delay inserted between consecutive commands during the initial
# state query, to avoid saturating the amplifier's UART. The C368 is known
# to misbehave (and in the worst case lock up its RS-232 interface) when
# flooded with back-to-back requests.
INTER_COMMAND_DELAY = 0.1

# Volume protection defaults (see NADAmplifier for details).
# 13 seconds matches the field-proven ESPHome startup protection window.
STARTUP_PROTECTION_WINDOW = 13.0


class PowerState(Enum):
    """Power state values used by the protocol."""

    ON = "On"
    OFF = "Off"


class VolumeDisplayMode(Enum):
    """How the amplifier displays (and reports) the volume."""

    DECIBEL = "Decibel"
    PERCENT = "Percent"


class ListeningMode(Enum):
    """Main.ListeningMode values."""

    STEREO = "Stereo"
    MONO = "Mono"
    REVERSED = "Reversed"
