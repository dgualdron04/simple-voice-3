"""Registro de uso (JSONL) para revisar errores sin supervisión y mejorar el sistema.

Qué guarda: cada frase transcrita, qué decidió el robot (ruta), qué respondió, tiempos y
confianza del STT; también lo que IGNORÓ (sin activación, ruido, eco), que es lo más útil para ajustar.
Qué NO guarda: audio, ni datos personales. Nombre/teléfono/correo dichos durante el registro, números,
correos y «me llamo X» se redactan antes de escribir. Retención corta (por defecto 14 días).
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DIGIT_WORDS = r"(?:cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)"
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|[\w.]+\s+arroba\s+\S+(?:\s+punto\s+\S+)*", re.IGNORECASE)
RE_DIGITS = re.compile(r"(?:\d[\s.\-]?){4,}")
RE_DIGIT_WORDS = re.compile(rf"(?:\b{DIGIT_WORDS}\b[\s,]*){{4,}}", re.IGNORECASE)
RE_NAME_INTRO = re.compile(r"((?:yo\s+)?(?:me llamo|mi nombre es|mi nombre completo es))\s+[^.,;!?]+", re.IGNORECASE)


def redact_text(text: str) -> str:
    text = RE_EMAIL.sub("<correo>", text)
    text = RE_DIGITS.sub("<num> ", text)
    text = RE_DIGIT_WORDS.sub("<num> ", text)
    text = RE_NAME_INTRO.sub(r"\1 <nombre>", text)
    return re.sub(r"\s+", " ", text).strip()


RE_NAME_ECHO = re.compile(r"((?:Tu nombre es|Mucho gusto,)) [^?.]+")


def redact_output(text: str) -> str:
    """Lo que el robot dice puede repetir datos personales (resumen de confirmación, nombre)."""
    if text.startswith("Confirmemos tus datos"):
        return "Confirmemos tus datos: <redactado>"
    return RE_NAME_ECHO.sub(r"\1 <nombre>", redact_text(text))


class UsageLog:
    def __init__(self, directory: Path, robot_id: str, enabled: bool = True,
                 retention_days: int = 14, log_text: bool = True):
        self.enabled = enabled
        self.dir = Path(directory)
        self.robot_id = robot_id
        self.log_text = log_text
        self.retention_days = retention_days
        self.session_id = uuid.uuid4().hex[:8]
        self.names: set[str] = set()        # nombres que la persona dio en esta sesión (memoria)
        self._lock = threading.Lock()
        self._seq = 0
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.purge()

    def purge(self) -> int:
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        removed = 0
        for path in self.dir.glob("*"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    def event(self, kind: str, **data) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._seq += 1
            record = {"ts": datetime.now().isoformat(timespec="milliseconds"), "run": self.session_id,
                      "n": self._seq, "robot": self.robot_id, "kind": kind, **data}
            path = self.dir / f"uso_{datetime.now():%Y-%m-%d}.jsonl"
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                pass            # el registro nunca debe tumbar la conversación

    def clean(self, text: str) -> str:
        """redact_text + cualquier nombre recordado en la sesión."""
        text = redact_text(text)
        for name in self.names:
            text = re.sub(rf"\b{re.escape(name)}\b", "<nombre>", text, flags=re.IGNORECASE)
        return text

    # --- helpers de alto nivel -------------------------------------------------
    def user_turn(self, raw: str, trace: dict, stt: dict | None, actions: list[dict], engine_ms: float) -> None:
        sensitive = bool(trace.get("sensitive"))
        if not self.log_text:
            shown = f"<texto omitido, {len(raw.split())} palabras>"
        elif sensitive:
            shown = f"<dato:{trace.get('flow_state')}>"
        else:
            shown = self.clean(raw)
        safe_actions = []
        for a in actions:
            a = dict(a)
            if "text" in a:
                a["text"] = self.clean(redact_output(a["text"])) if self.log_text else "<omitido>"
            if "plain" in a:
                a["plain"] = self.clean(a["plain"]) if self.log_text else "<omitido>"
            safe_actions.append(a)
        self.event("turn", heard=shown, route=trace.get("route"), flow_state=trace.get("flow_state"),
                   session=trace.get("session"), memory=trace.get("memory"), stt=stt, actions=safe_actions,
                   engine_ms=round(engine_ms, 1))


class Stopwatch:
    def __init__(self):
        self.t0 = time.monotonic()

    def ms(self) -> float:
        return (time.monotonic() - self.t0) * 1000
