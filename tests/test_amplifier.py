"""Integration-style tests for NADAmplifier using a simulated amplifier.

These tests patch ``serialx.open_serial_connection`` with an in-memory
transport that emulates a NAD C368, so the full read loop, query matching
and volume protections are exercised without hardware.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nad_rs232 import MODELS, NADAmplifier
from nad_rs232.amplifier import STARTUP_PROTECTION_WINDOW  # noqa: F401


class FakeNAD:
    """A minimal in-memory NAD C368 emulator."""

    def __init__(self) -> None:
        self.state: dict[str, str] = {
            "Main.Model": "C368",
            "Main.Version": "1.61",
            "Main.Power": "Off",
            "Main.Volume": "-25.5",
            "Main.Mute": "Off",
            "Main.Source": "1",
            "Main.Sources": "3",
            "Main.SpeakerA": "On",
            "Main.SpeakerB": "Off",
            "Main.ListeningMode": "Stereo",
            "Main.VolumeDisplayMode": "Decibel",
            "Source1.Name": "Optical 1",
            "Source1.Enabled": "Yes",
            "Source2.Name": "Phono",
            "Source2.Enabled": "Yes",
            "Source3.Name": "Line 1",
            "Source3.Enabled": "No",
        }
        self.reader = asyncio.StreamReader()
        self.writer = FakeWriter(self)
        self.received: list[str] = []

    def handle(self, message: str) -> None:
        """Process one command from the library and emit the response."""
        self.received.append(message)

        if message.endswith("?"):
            variable = message[:-1]
            if variable in self.state:
                self.respond(variable, self.state[variable])
        elif "=" in message:
            variable, _, value = message.partition("=")
            if variable in self.state:
                self.state[variable] = value
                self.respond(variable, value)
        elif message.endswith("+") or message.endswith("-"):
            variable = message[:-1]
            if variable == "Main.Volume":
                step = 0.5 if message.endswith("+") else -0.5
                new = float(self.state[variable]) + step
                self.state[variable] = f"{new:.1f}"
                self.respond(variable, self.state[variable])

    def respond(self, variable: str, value: str) -> None:
        self.reader.feed_data(f"{variable}={value}\r".encode())

    def push_event(self, variable: str, value: str) -> None:
        """Simulate an unsolicited event (front panel / remote change)."""
        self.state[variable] = value
        self.respond(variable, value)


class FakeWriter:
    """Mimics the subset of serialx.SerialStreamWriter the library uses."""

    def __init__(self, fake: FakeNAD) -> None:
        self._fake = fake
        self._buf = b""
        self._closed = False

    def write(self, data: bytes) -> None:
        self._buf += data
        while b"\r" in self._buf:
            line, self._buf = self._buf.split(b"\r", 1)
            if line:
                self._fake.handle(line.decode())

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self._closed = True
        self._fake.reader.feed_eof()

    async def wait_closed(self) -> None:
        await asyncio.sleep(0)


@pytest.fixture
async def fake_nad(monkeypatch: pytest.MonkeyPatch) -> FakeNAD:
    # Created inside the running event loop so the StreamReader binds to it.
    fake = FakeNAD()

    async def _fake_open(port: str, **kwargs: Any):
        return fake.reader, fake.writer

    monkeypatch.setattr(
        "nad_rs232.amplifier.serialx.open_serial_connection", _fake_open
    )
    return fake


async def _settle() -> None:
    """Let the read loop process pending data."""
    for _ in range(10):
        await asyncio.sleep(0)


async def test_connect_and_query_state(fake_nad: FakeNAD) -> None:
    amp = NADAmplifier("/dev/fake", model=MODELS["c368"])
    await amp.connect()
    assert amp.connected
    assert amp.state.model == "C368"

    await amp.query_state()
    state = amp.state
    assert state.power is False
    assert state.volume_db == -25.5
    assert state.volume_percent == pytest.approx(50.0)
    assert state.mute is False
    assert state.source == 1
    assert state.source_count == 3
    assert state.sources[1].display_name == "Optical 1"
    assert state.sources[2].display_name == "Phono"
    assert state.sources[3].enabled is False
    assert state.speaker_a is True

    await amp.disconnect()
    assert not amp.connected


async def test_commands_update_state(fake_nad: FakeNAD) -> None:
    amp = NADAmplifier("/dev/fake", model=MODELS["c368"])
    await amp.connect()

    await amp.power_on()
    await _settle()
    assert amp.state.power is True

    await amp.set_volume_db(-30.0)
    await _settle()
    assert amp.state.volume_db == -30.0

    await amp.set_volume_percent(60.0)
    await _settle()
    assert amp.state.volume_db == -15.0

    await amp.mute_on()
    await _settle()
    assert amp.state.mute is True

    await amp.select_source(2)
    await _settle()
    assert amp.state.source == 2

    await amp.disconnect()


async def test_unsolicited_events_notify_subscribers(fake_nad: FakeNAD) -> None:
    amp = NADAmplifier("/dev/fake", model=MODELS["c368"])
    await amp.connect()

    updates: list[Any] = []
    amp.subscribe(updates.append)

    # User turns the volume knob on the amplifier itself.
    fake_nad.push_event("Main.Volume", "-20.0")
    await _settle()

    assert updates
    assert updates[-1].volume_db == -20.0

    await amp.disconnect()
    # Disconnect notifies with None.
    assert updates[-1] is None


async def test_max_volume_ceiling(fake_nad: FakeNAD) -> None:
    # Ceiling at 70% (== -7.5 dB on the measured curve).
    amp = NADAmplifier("/dev/fake", model=MODELS["c368"], max_volume_db=-7.5)
    await amp.connect()

    await amp.set_volume_db(5.0)  # way above the ceiling
    await _settle()
    assert amp.state.volume_db == -7.5

    await amp.set_volume_percent(100.0)
    await _settle()
    assert amp.state.volume_db == -7.5

    # volume_up at the ceiling is suppressed.
    await amp.volume_up()
    await _settle()
    assert amp.state.volume_db == -7.5

    # Manual change on the amplifier itself is NOT overridden.
    fake_nad.push_event("Main.Volume", "0.0")
    await _settle()
    assert amp.state.volume_db == 0.0

    await amp.disconnect()


async def test_startup_protection(fake_nad: FakeNAD) -> None:
    # Startup limit at 40% (== -35.0 dB).
    amp = NADAmplifier(
        "/dev/fake", model=MODELS["c368"], startup_volume_db=-35.0
    )
    await amp.connect()

    # Amplifier powers on...
    fake_nad.push_event("Main.Power", "On")
    await _settle()
    # ...and reports a dangerously high boot volume.
    fake_nad.push_event("Main.Volume", "0.0")
    await _settle()
    await _settle()

    # The library must have issued a corrective volume command.
    assert "Main.Volume=-35.0" in fake_nad.received
    assert amp.state.volume_db == -35.0

    await amp.disconnect()


async def test_corrupted_volume_frame_ignored(fake_nad: FakeNAD) -> None:
    amp = NADAmplifier("/dev/fake", model=MODELS["c368"])
    await amp.connect()

    fake_nad.push_event("Main.Volume", "-20.0")
    await _settle()
    assert amp.state.volume_db == -20.0

    # Corrupted frame seen in the field: must not poison the state.
    fake_nad.reader.feed_data(b"Main.Volume=100208%\r")
    fake_nad.reader.feed_data(b"!!garbage!!\r")
    await _settle()
    assert amp.state.volume_db == -20.0

    await amp.disconnect()
