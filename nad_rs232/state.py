"""Runtime state dataclasses for nad_rs232."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .const import ListeningMode, VolumeDisplayMode
from .protocol import db_to_percent


@dataclass
class SourceInfo:
    """Information about one selectable input source."""

    number: int
    name: str | None = None
    enabled: bool = True

    def copy(self) -> SourceInfo:
        return replace(self)

    @property
    def display_name(self) -> str:
        """Return the user-facing name of this source."""
        return self.name or f"Source {self.number}"


@dataclass
class AmplifierState:
    """Current state of the NAD amplifier."""

    power: bool | None = None
    #: Main volume in dB (always normalized to dB internally).
    volume_db: float | None = None
    mute: bool | None = None
    #: Currently selected source number (1-based).
    source: int | None = None
    #: Total number of sources reported by Main.Sources?
    source_count: int | None = None
    #: Per-source info, keyed by source number.
    sources: dict[int, SourceInfo] = field(default_factory=dict)
    speaker_a: bool | None = None
    speaker_b: bool | None = None
    listening_mode: ListeningMode | None = None
    #: How the amplifier currently displays/reports the volume.
    volume_display_mode: VolumeDisplayMode | None = None
    model: str | None = None
    version: str | None = None

    @property
    def volume_percent(self) -> float | None:
        """Return the volume as the amplifier's front panel percentage."""
        if self.volume_db is None:
            return None
        return db_to_percent(self.volume_db)

    def copy(self) -> AmplifierState:
        return replace(
            self,
            sources={num: info.copy() for num, info in self.sources.items()},
        )
