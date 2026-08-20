"""Amplifier implementation for nad_rs232."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import serialx

from .const import (
    BAUD_RATE,
    COMMAND_TIMEOUT,
    CR,
    INTER_COMMAND_DELAY,
    LF,
    PROBE_TIMEOUT,
    STARTUP_PROTECTION_WINDOW,
    ListeningMode,
    VolumeDisplayMode,
)
from .protocol import (
    PendingQuery,
    format_volume_db,
    parse_message,
    parse_volume_value,
    percent_to_db,
    round_half_db,
)
from .state import AmplifierState, SourceInfo

if TYPE_CHECKING:
    from .models import AmplifierModel

_LOGGER = logging.getLogger(__name__)

StateCallback = Callable[[AmplifierState | None], None]


class NADAmplifier:
    """Async controller for a NAD amplifier over RS-232 (protocol v2.x).

    The ``port`` argument is any URL supported by ``serialx``: a local
    serial port such as ``/dev/ttyUSB0``, or an ESPHome serial proxy URL,
    transparently.

    Volume safety
    -------------
    Two independent, optional protections guard the speakers:

    * ``max_volume_db`` — a hard ceiling applied to every volume command
      issued *through this library*. Manual control on the amplifier's
      front panel or remote is never overridden. Defaults to the model's
      hardware maximum (i.e. no extra limit).
    * ``startup_volume_db`` — for ``STARTUP_PROTECTION_WINDOW`` seconds
      after the amplifier reports powering on, any volume report above
      ``startup_trigger_db`` triggers an immediate corrective command
      lowering the volume to ``startup_volume_db``. The trigger threshold
      is deliberately higher than the cap so that a volume intentionally
      set just after power-on (e.g. 45%) is not fought by the protection,
      while a dangerous boot volume (e.g. 80%) is. This complements the
      amplifier's own native boot limit (55% on the C368). Set
      ``startup_volume_db`` to ``None`` to disable.
    """

    def __init__(
        self,
        port: str,
        *,
        model: AmplifierModel | None = None,
        max_volume_db: float | None = None,
        startup_volume_db: float | None = percent_to_db(40.0),
        startup_trigger_db: float = percent_to_db(60.0),
    ) -> None:
        self._port = port
        self._model = model
        self._max_volume_db = max_volume_db
        self._startup_volume_db = startup_volume_db
        self._startup_trigger_db = startup_trigger_db

        self._reader: asyncio.StreamReader | None = None
        self._writer: serialx.SerialStreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._state = AmplifierState()
        self._subscribers: list[StateCallback] = []
        self._pending_queries: list[PendingQuery] = []
        self._write_lock = asyncio.Lock()
        self._connected = False
        # Monotonic timestamp of the last Off -> On transition, used by the
        # startup volume protection.
        self._power_on_time: float | None = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def model(self) -> AmplifierModel | None:
        """Return the configured amplifier model, if any."""
        return self._model

    @property
    def state(self) -> AmplifierState:
        """Return a copy of the current state."""
        return self._state.copy()

    @property
    def connected(self) -> bool:
        """Return whether the serial connection is open."""
        return self._connected

    @property
    def max_volume_db(self) -> float | None:
        """Return the configured library-side volume ceiling in dB."""
        return self._max_volume_db

    @max_volume_db.setter
    def max_volume_db(self, value: float | None) -> None:
        self._max_volume_db = value

    def subscribe(self, callback: StateCallback) -> Callable[[], None]:
        """Subscribe to state changes. Returns an unsubscribe function.

        The callback receives a copy of the new state, or ``None`` when
        the connection is lost.
        """
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the serial connection and verify the amplifier responds."""
        self._reader, self._writer = await serialx.open_serial_connection(
            self._port,
            baudrate=BAUD_RATE,
        )
        self._connected = True
        self._read_task = asyncio.create_task(self._read_loop())

        try:
            model = await self._query("Main.Model", timeout=PROBE_TIMEOUT)
        except TimeoutError:
            await self.disconnect()
            raise ConnectionError(
                f"No response from NAD amplifier on {self._port}"
            ) from None

        self._state.model = model
        _LOGGER.info("Connected to NAD %s on %s", model, self._port)

    async def disconnect(self) -> None:
        """Close the serial connection."""
        await self._teardown()
        _LOGGER.info("Disconnected from NAD amplifier")

    async def query_state(self) -> None:
        """Query all initial state from the amplifier.

        A small delay is inserted between queries: the amplifier's UART
        is easily saturated and may stop responding when flooded.
        """
        for variable in (
            "Main.Version",
            "Main.Power",
            "Main.VolumeDisplayMode",
            "Main.Volume",
            "Main.Mute",
            "Main.Source",
            "Main.Sources",
            "Main.SpeakerA",
            "Main.SpeakerB",
            "Main.ListeningMode",
        ):
            try:
                await self._query(variable)
            except TimeoutError:
                _LOGGER.debug("No response to %s? query", variable)
            await asyncio.sleep(INTER_COMMAND_DELAY)

        await self._query_sources()

    async def _query_sources(self) -> None:
        """Query the name and enabled flag of every source."""
        count = self._state.source_count
        if count is None:
            count = (
                self._model.default_source_count if self._model is not None else 10
            )

        for number in range(1, count + 1):
            for suffix in ("Name", "Enabled"):
                try:
                    await self._query(f"Source{number}.{suffix}")
                except TimeoutError:
                    _LOGGER.debug("No response for Source%s.%s", number, suffix)
                await asyncio.sleep(INTER_COMMAND_DELAY)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def power_on(self) -> None:
        """Turn the amplifier on."""
        await self._send_command("Main.Power", "=", "On")

    async def power_off(self) -> None:
        """Put the amplifier in standby."""
        await self._send_command("Main.Power", "=", "Off")

    async def set_volume_db(self, db: float) -> None:
        """Set the volume in dB (clamped to the configured safety ceiling)."""
        db = self._clamp_volume(db)
        await self._send_command("Main.Volume", "=", format_volume_db(db))

    async def set_volume_percent(self, percent: float) -> None:
        """Set the volume as a front panel percentage (0-100)."""
        await self.set_volume_db(percent_to_db(percent))

    async def volume_up(self) -> None:
        """Increase the volume by one 0.5 dB step.

        When a safety ceiling is configured and the current volume is
        already at or above it, the step is suppressed.
        """
        if (
            self._max_volume_db is not None
            and self._state.volume_db is not None
            and self._state.volume_db >= self._max_volume_db
        ):
            _LOGGER.debug("Volume up suppressed by safety ceiling")
            return
        await self._send_command("Main.Volume", "+", "")

    async def volume_down(self) -> None:
        """Decrease the volume by one 0.5 dB step."""
        await self._send_command("Main.Volume", "-", "")

    async def mute_on(self) -> None:
        """Mute the amplifier."""
        await self._send_command("Main.Mute", "=", "On")

    async def mute_off(self) -> None:
        """Unmute the amplifier."""
        await self._send_command("Main.Mute", "=", "Off")

    async def select_source(self, number: int) -> None:
        """Select an input source by number (1-based)."""
        await self._send_command("Main.Source", "=", str(number))

    async def set_speaker_a(self, on: bool) -> None:
        """Enable or disable speaker output A."""
        await self._send_command("Main.SpeakerA", "=", "On" if on else "Off")

    async def set_speaker_b(self, on: bool) -> None:
        """Enable or disable speaker output B."""
        await self._send_command("Main.SpeakerB", "=", "On" if on else "Off")

    async def send_raw(self, variable: str, operator: str, value: str = "") -> None:
        """Send an arbitrary protocol command (escape hatch).

        Example: ``await amp.send_raw("Main.Bass", "=", "-2")``.
        """
        await self._send_command(variable, operator, value)

    async def query(self, variable: str) -> str:
        """Query an arbitrary protocol variable and return the raw value."""
        return await self._query(variable)

    # ------------------------------------------------------------------
    # Volume safety helpers
    # ------------------------------------------------------------------

    def _clamp_volume(self, db: float) -> float:
        """Clamp a requested volume to the hardware and safety limits."""
        min_db = self._model.min_volume_db if self._model is not None else -70.0
        max_db = self._model.max_volume_db if self._model is not None else 12.0
        if self._max_volume_db is not None:
            max_db = min(max_db, self._max_volume_db)

        clamped = min(max(db, min_db), max_db)
        if clamped != db:
            _LOGGER.info(
                "Requested volume %.1f dB clamped to %.1f dB", db, clamped
            )
        return round_half_db(clamped)

    def _check_startup_protection(self, volume_db: float) -> None:
        """Issue a corrective command if a dangerous boot volume is seen."""
        if (
            self._startup_volume_db is None
            or self._power_on_time is None
            or time.monotonic() - self._power_on_time > STARTUP_PROTECTION_WINDOW
        ):
            return
        if volume_db <= self._startup_trigger_db:
            return

        _LOGGER.warning(
            "Startup protection: volume %.1f dB exceeds %.1f dB, lowering",
            volume_db,
            self._startup_volume_db,
        )
        asyncio.create_task(
            self._send_command(
                "Main.Volume", "=", format_volume_db(self._startup_volume_db)
            )
        )

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    async def _send_command(self, variable: str, operator: str, value: str) -> None:
        """Send a command, framed with a leading and trailing <CR>."""
        assert self._writer is not None
        msg = f"\r{variable}{operator}{value}\r".encode("ascii")
        _LOGGER.debug("Sending: %s", msg)
        try:
            async with self._write_lock:
                self._writer.write(msg)
                await self._writer.drain()
        except Exception:
            _LOGGER.exception("Error writing to serial port")
            await self._teardown()
            raise

    async def _query(self, variable: str, timeout: float = COMMAND_TIMEOUT) -> str:
        """Send a query and wait for the matching response."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        pending = PendingQuery(variable=variable, future=future)
        self._pending_queries.append(pending)

        try:
            await self._send_command(variable, "?", "")
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            if pending in self._pending_queries:
                self._pending_queries.remove(pending)

    async def _teardown(self) -> None:
        """Tear down the connection after an error or on disconnect."""
        if not self._connected:
            return
        self._connected = False

        current = asyncio.current_task()
        if self._read_task is not None and self._read_task is not current:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        self._read_task = None

        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

        self._notify_subscribers()

    async def _read_loop(self) -> None:
        """Continuously read and process messages from the amplifier."""
        assert self._reader is not None
        buf = b""

        while self._connected:
            try:
                data = await self._reader.read(256)
            except Exception:
                if not self._connected:
                    return
                _LOGGER.exception("Error reading from serial port")
                await self._teardown()
                return

            if not data:
                _LOGGER.warning("Serial connection closed")
                await self._teardown()
                return

            buf += data.replace(LF, CR)

            while CR in buf:
                line, buf = buf.split(CR, 1)
                if not line:
                    continue
                message = line.decode("ascii", errors="replace").strip()
                if message:
                    self._process_message(message)

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    @staticmethod
    def _set_attr_value(target: object, attr: str, new_value: object) -> bool:
        """Set an attribute only when the value changed."""
        if getattr(target, attr) == new_value:
            return False
        setattr(target, attr, new_value)
        return True

    def _process_message(self, message: str) -> None:
        """Parse and process a message from the amplifier."""
        _LOGGER.debug("Received: %s", message)

        parsed = parse_message(message)
        if parsed is None:
            _LOGGER.debug("Ignoring malformed message: %s", message)
            return
        variable, value = parsed

        changed = self._update_state(variable, value)

        for pending in list(self._pending_queries):
            if pending.variable == variable and not pending.future.done():
                pending.future.set_result(value)

        if changed:
            self._notify_subscribers()

    def _update_state(self, variable: str, value: str) -> bool:
        """Apply a (variable, value) pair to the state. Return True if changed."""
        state = self._state

        if variable == "Main.Power":
            new_power = value == "On"
            changed = self._set_attr_value(state, "power", new_power)
            if changed and new_power:
                # Arm the startup volume protection window.
                self._power_on_time = time.monotonic()
            return changed

        if variable == "Main.Volume":
            percent_mode = state.volume_display_mode is VolumeDisplayMode.PERCENT
            volume_db = parse_volume_value(value, percent_mode=percent_mode)
            if volume_db is None:
                _LOGGER.warning("Ignoring invalid volume value: %s", value)
                return False
            self._check_startup_protection(volume_db)
            return self._set_attr_value(state, "volume_db", volume_db)

        if variable == "Main.Mute":
            return self._set_attr_value(state, "mute", value == "On")

        if variable == "Main.Source":
            try:
                number = int(value)
            except ValueError:
                _LOGGER.warning("Ignoring invalid source value: %s", value)
                return False
            return self._set_attr_value(state, "source", number)

        if variable == "Main.Sources":
            try:
                count = int(value)
            except ValueError:
                _LOGGER.warning("Ignoring invalid source count: %s", value)
                return False
            return self._set_attr_value(state, "source_count", count)

        if variable == "Main.SpeakerA":
            return self._set_attr_value(state, "speaker_a", value == "On")

        if variable == "Main.SpeakerB":
            return self._set_attr_value(state, "speaker_b", value == "On")

        if variable == "Main.ListeningMode":
            try:
                mode = ListeningMode(value)
            except ValueError:
                _LOGGER.warning("Unknown listening mode: %s", value)
                return False
            return self._set_attr_value(state, "listening_mode", mode)

        if variable == "Main.VolumeDisplayMode":
            try:
                mode = VolumeDisplayMode(value)
            except ValueError:
                _LOGGER.warning("Unknown volume display mode: %s", value)
                return False
            return self._set_attr_value(state, "volume_display_mode", mode)

        if variable == "Main.Model":
            return self._set_attr_value(state, "model", value)

        if variable == "Main.Version":
            return self._set_attr_value(state, "version", value)

        if variable.startswith("Source") and "." in variable:
            return self._update_source(variable, value)

        _LOGGER.debug("Unhandled variable: %s=%s", variable, value)
        return False

    def _update_source(self, variable: str, value: str) -> bool:
        """Process SourceN.Name / SourceN.Enabled messages."""
        prefix, _, suffix = variable.partition(".")
        try:
            number = int(prefix.removeprefix("Source"))
        except ValueError:
            return False

        info = self._state.sources.setdefault(number, SourceInfo(number=number))

        if suffix == "Name":
            return self._set_attr_value(info, "name", value)
        if suffix == "Enabled":
            return self._set_attr_value(info, "enabled", value == "Yes")
        return False

    def _notify_subscribers(self) -> None:
        """Notify all subscribers of a state change or disconnect."""
        state = self._state.copy() if self._connected else None
        for callback in self._subscribers:
            try:
                callback(state)
            except Exception:
                _LOGGER.exception("Error in state change callback %s", callback)
