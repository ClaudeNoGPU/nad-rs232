# nad-rs232

Async Python library to control NAD amplifiers (C338 / C368 / C388) over
RS-232, built on [serialx](https://github.com/puddly/serialx).

Because it uses `serialx`, the same code works over a local serial port
(`/dev/ttyUSB0`, a USB-to-serial adapter) **or** an
[ESPHome serial proxy](https://esphome.io/components/serial_proxy/) in
Home Assistant, transparently.

## Features

- Implements the NAD ASCII RS-232 protocol v2.x (115200 8N1)
- Power, volume (dB or front-panel percent), mute, source selection,
  speakers A/B, plus a raw-command escape hatch
- **Local push**: unsolicited events (front panel / remote changes) update
  the state and notify subscribers — no polling
- Dynamic source discovery (`Main.Sources?`, `SourceN.Name?`,
  `SourceN.Enabled?`): your custom source names are picked up automatically
- dB ↔ percent conversion using a non-linear curve measured on a real C368,
  matching the amplifier's own front panel scale exactly
- Robust input validation (corrupted UART frames are rejected)
- Optional speaker-safety features:
  - a hard volume ceiling applied to commands issued through the library
  - a startup protection that lowers a dangerously high boot volume

## Usage

```python
import asyncio
from nad_rs232 import MODELS, NADAmplifier, percent_to_db

async def main():
    amp = NADAmplifier(
        "/dev/ttyUSB0",
        model=MODELS["c368"],
        max_volume_db=percent_to_db(70),   # safety ceiling at 70%
    )
    await amp.connect()
    await amp.query_state()

    amp.subscribe(lambda state: print("update:", state))

    await amp.power_on()
    await amp.set_volume_percent(50)       # == -25.5 dB on the C368
    await amp.select_source(5)

    await amp.disconnect()

asyncio.run(main())
```

## CLI

```bash
python -m nad_rs232 /dev/ttyUSB0 status
python -m nad_rs232 /dev/ttyUSB0 volume 50%
python -m nad_rs232 /dev/ttyUSB0 monitor
python -m nad_rs232 /dev/ttyUSB0 raw "Main.Bass?"
```

## Tests

```bash
pip install -e ".[test]"
pytest
```

The test suite includes an in-memory NAD C368 emulator exercising the full
read loop, query matching and both volume protections.
