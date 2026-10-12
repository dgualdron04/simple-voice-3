"""Carga de configuración por robot (robots/<nombre>.yaml) sobre defaults.

Un solo código base: lo que cambia entre robots vive en el YAML.
El robot activo sale de la variable de entorno ROBOT (default: laptop).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
ROBOTS_DIR = BASE_DIR / "robots"

DEFAULTS: dict = {
    "robot": {"id": "laptop", "name": "Zuu", "model": "laptop", "aliases": []},
    "language": "es",
    "institution": "Universidad de Investigación y Desarrollo UDI",
    "paths": {
        "database": "data/zuu.db",
        "xml_dir": "data/rag",
        "registrations_db": "data/registros.db",
        "registrations_key": "data/registros.key",
        "export_dir": "data/export",
    },
    "audio": {
        "input_device": None,       # índice o fragmento del nombre; None = default
        "output_device": None,
        "sample_rate_in": 16000,
        "sample_rate_out": 22050,
        "barge_in": False,          # False = half-duplex (sin AEC el robot no se oye a sí mismo)
        "post_speech_mute_secs": 0.7,
    },
    "vad": {"confidence": 0.7, "start_secs": 0.25, "stop_secs": 0.6, "min_volume": 0.5},
    "stt": {
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 1,
        "no_speech_prob": 0.6,
        "min_chars": 3,
        "initial_prompt": "Conversación en español de Colombia con un asistente de la universidad UDI.",
    },
    "llm": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:3b-instruct-zuu",
        "max_tokens": 90,
        "temperature": 0.4,
        "history_messages": 6,
    },
    "tts": {
        "engine": "piper",
        "voice": "test_epoch10100/zuu_10100",   # relativo a voices_dir, sin .onnx
        "voices_dir": "voices",
        "length_scale": None,
        "pronunciations": {"Teamsu": "Tim-su", "Axl": "Áxel", "UDI": "U D I"},
    },
    "wake": {
        "mode": "text",             # text | none
        "session_secs": 45,
        "greetings": ["hola", "buenas", "buenos dias", "buen dia", "buenas tardes", "buenas noches", "hey", "oye"],
    },
    "dialog": {
        "mode": "normal",           # normal | feria (respuestas más cortas)
        "max_voice_chars": 420,
        "feria_max_voice_chars": 240,
        "echo_similarity": 0.8,     # descarta transcripciones casi idénticas a lo que dijo el robot
    },
    "gestures": {"adapter": "simulated", "enabled": True, "allowed": ["saludar", "dar_mano", "señalar"]},
    "registration": {
        "retries_per_field": 2,
        "idle_timeout_secs": 60,
        "retention_days": 30,
    },
    "persona": "Eres un asistente virtual amable y claro. Respondes siempre en español, en máximo dos frases cortas.",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _validate(settings: dict) -> None:
    if settings["wake"]["mode"] not in ("text", "none"):
        raise ValueError("wake.mode debe ser 'text' o 'none'")
    if settings["dialog"]["mode"] not in ("normal", "feria"):
        raise ValueError("dialog.mode debe ser 'normal' o 'feria'")
    if not settings["robot"].get("name"):
        raise ValueError("robot.name es obligatorio")
    if settings["tts"]["engine"] not in ("piper", "kokoro"):
        raise ValueError("tts.engine debe ser 'piper' o 'kokoro'")


_settings: dict | None = None


def load_robot(robot: str | None = None) -> dict:
    """Carga robots/<robot>.yaml sobre DEFAULTS y fija la config global."""
    global _settings
    robot = robot or os.environ.get("ROBOT", "laptop")
    path = ROBOTS_DIR / f"{robot}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in ROBOTS_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"No existe {path}. Robots disponibles: {available}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = _deep_merge(DEFAULTS, raw)
    settings["robot"].setdefault("id", robot)
    _validate(settings)
    _settings = settings
    return settings


def get_settings() -> dict:
    return _settings if _settings is not None else load_robot()


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path
