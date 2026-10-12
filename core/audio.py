"""Resolución de dispositivos de audio por índice o fragmento de nombre (config por robot)."""
from __future__ import annotations


def list_devices() -> list[dict]:
    import pyaudio
    pa = pyaudio.PyAudio()
    try:
        return [pa.get_device_info_by_index(i) for i in range(pa.get_device_count())]
    finally:
        pa.terminate()


def resolve_device(spec, kind: str) -> int | None:
    """spec: None (default del sistema) | índice | fragmento del nombre. kind: input|output."""
    if spec is None:
        return None
    if isinstance(spec, int) or str(spec).isdigit():
        return int(spec)
    key = "maxInputChannels" if kind == "input" else "maxOutputChannels"
    for dev in list_devices():
        if dev[key] > 0 and str(spec).lower() in dev["name"].lower():
            return int(dev["index"])
    raise ValueError(f"No encontré un dispositivo de {kind} que contenga '{spec}'. "
                     f"Usa `python -m core.main --devices` para ver los disponibles.")
