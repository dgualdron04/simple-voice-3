"""Punto de entrada:  python -m core.main --robot laptop"""
from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Asistente de voz UDI (Zuu / Axl / Teamsu)")
    parser.add_argument("--robot", default=None, help="laptop | g1 | h2 | go2x (default: env ROBOT o laptop)")
    parser.add_argument("--devices", action="store_true", help="lista dispositivos de audio y sale")
    args = parser.parse_args()

    if args.devices:
        from core.audio import list_devices
        for d in list_devices():
            print(f"{int(d['index']):3d}  in={d['maxInputChannels']} out={d['maxOutputChannels']}  {d['name']}")
        return 0

    from core.pipeline import run
    try:
        asyncio.run(run(args.robot))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
