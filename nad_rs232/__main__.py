"""CLI to test a NAD amplifier over RS-232.

Usage examples::

    python -m nad_rs232 /dev/ttyUSB0 status
    python -m nad_rs232 /dev/ttyUSB0 on
    python -m nad_rs232 /dev/ttyUSB0 off
    python -m nad_rs232 /dev/ttyUSB0 volume -25.5
    python -m nad_rs232 /dev/ttyUSB0 volume 50%
    python -m nad_rs232 /dev/ttyUSB0 mute on
    python -m nad_rs232 /dev/ttyUSB0 source 5
    python -m nad_rs232 /dev/ttyUSB0 monitor
    python -m nad_rs232 /dev/ttyUSB0 raw "Main.Bass?"

The port can also be an ESPHome serial proxy URL once your proxy device
is flashed (see the serialx documentation for the URL format).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import MODELS, AmplifierState, NADAmplifier


def _format_db(db: float | None) -> str:
    if db is None:
        return "?"
    sign = "+" if db > 0 else ""
    return f"{sign}{db:.1f} dB"


def _print_state(state: AmplifierState) -> None:
    print()
    print("=== NAD Amplifier Status ===")
    print()
    print(f"  Model:      {state.model or '?'}")
    print(f"  Version:    {state.version or '?'}")
    power = "ON" if state.power else "OFF" if state.power is not None else "?"
    print(f"  Power:      {power}")
    pct = state.volume_percent
    pct_str = f" ({pct:.0f}%)" if pct is not None else ""
    print(f"  Volume:     {_format_db(state.volume_db)}{pct_str}")
    mute = "ON" if state.mute else "OFF" if state.mute is not None else "?"
    print(f"  Mute:       {mute}")

    if state.source is not None:
        info = state.sources.get(state.source)
        name = info.display_name if info else f"Source {state.source}"
        print(f"  Source:     {state.source} ({name})")
    else:
        print("  Source:     ?")

    spk_a = "ON" if state.speaker_a else "OFF" if state.speaker_a is not None else "?"
    spk_b = "ON" if state.speaker_b else "OFF" if state.speaker_b is not None else "?"
    print(f"  Speaker A:  {spk_a}")
    print(f"  Speaker B:  {spk_b}")
    if state.listening_mode is not None:
        print(f"  Mode:       {state.listening_mode.value}")

    if state.sources:
        print()
        print("  Sources:")
        for number in sorted(state.sources):
            info = state.sources[number]
            flag = "" if info.enabled else "  (disabled)"
            print(f"    {number:>2d}: {info.display_name}{flag}")
    print()


async def _run(args: argparse.Namespace) -> None:
    amp = NADAmplifier(args.port, model=MODELS[args.model])

    print(f"Connecting to {args.port}...")
    try:
        await amp.connect()
    except (ConnectionError, OSError, TimeoutError) as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "status":
            print("Querying amplifier state...")
            await amp.query_state()
            _print_state(amp.state)

        elif args.command == "on":
            await amp.power_on()
            print("Power on command sent.")

        elif args.command == "off":
            await amp.power_off()
            print("Power off command sent.")

        elif args.command == "volume":
            value: str = args.value
            if value.endswith("%"):
                await amp.set_volume_percent(float(value[:-1]))
            else:
                await amp.set_volume_db(float(value))
            await asyncio.sleep(0.3)
            confirmed = await amp.query("Main.Volume")
            print(f"Volume set. Amplifier reports: {confirmed}")

        elif args.command == "mute":
            if args.value == "on":
                await amp.mute_on()
            else:
                await amp.mute_off()
            print(f"Mute {args.value} command sent.")

        elif args.command == "source":
            await amp.select_source(args.number)
            await asyncio.sleep(0.3)
            confirmed = await amp.query("Main.Source")
            print(f"Source selected. Amplifier reports: {confirmed}")

        elif args.command == "raw":
            cmd: str = args.cmd
            if cmd.endswith("?"):
                response = await amp.query(cmd[:-1])
                print(f"{cmd[:-1]}={response}")
            else:
                variable, _, value = cmd.partition("=")
                await amp.send_raw(variable, "=", value)
                print("Command sent.")

        elif args.command == "monitor":
            print("Monitoring amplifier events (Ctrl+C to stop)...")

            def _on_state(state: AmplifierState | None) -> None:
                if state is None:
                    print("** Disconnected **")
                    return
                pct = state.volume_percent
                pct_str = f" ({pct:.0f}%)" if pct is not None else ""
                print(
                    f"power={state.power} "
                    f"volume={_format_db(state.volume_db)}{pct_str} "
                    f"mute={state.mute} source={state.source}"
                )

            amp.subscribe(_on_state)
            try:
                while amp.connected:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass

    finally:
        if amp.connected:
            await amp.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m nad_rs232",
        description="Test a NAD amplifier over RS-232",
    )
    parser.add_argument("port", help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument(
        "--model",
        default="c368",
        choices=sorted(MODELS),
        help="Amplifier model (default: c368)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable protocol debug logging"
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Query and print the full amplifier state")
    sub.add_parser("on", help="Turn the amplifier on")
    sub.add_parser("off", help="Put the amplifier in standby")

    p_vol = sub.add_parser("volume", help="Set the volume")
    p_vol.add_argument("value", help="Volume in dB (e.g. -25.5) or percent (e.g. 50%%)")

    p_mute = sub.add_parser("mute", help="Mute or unmute")
    p_mute.add_argument("value", choices=["on", "off"])

    p_src = sub.add_parser("source", help="Select an input source")
    p_src.add_argument("number", type=int, help="Source number (1-based)")

    p_raw = sub.add_parser("raw", help="Send a raw protocol command")
    p_raw.add_argument("cmd", help='e.g. "Main.Bass?" or "Main.Bass=-2"')

    sub.add_parser("monitor", help="Print state changes as they happen")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
