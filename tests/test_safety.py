"""Garantías de seguridad: el asistente nunca toca locomoción y solo ejecuta gestos de la lista blanca."""
import re
from pathlib import Path

from adapters.base import GestureRunner, SimulatedAdapter

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = re.compile(r"^\s*(?:from|import)\s+.*(?:loco|LocoClient|sport_client|SportClient|g1\.loco|go2\.sport)", re.I | re.M)


def test_no_locomotion_imports_outside_legacy():
    offenders = []
    for folder in ("core", "adapters", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if FORBIDDEN.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"Importan locomoción: {offenders}"


def test_g1_adapter_has_no_locomotion_calls():
    code = (ROOT / "adapters" / "g1.py").read_text(encoding="utf-8")
    code_without_comments = "\n".join(line.split("#")[0] for line in code.splitlines())
    for word in ("LocoClient(", "HighStand", "Move(", "SetVelocity", "StandUp", "Damp("):
        assert word not in code_without_comments


def test_gesture_runner_rejects_unlisted_gestures():
    adapter = SimulatedAdapter()
    runner = GestureRunner(adapter, allowed=["saludar"], enabled=True)
    assert runner.request("caminar") is False
    assert runner.request("saltar") is False
    assert adapter.performed == []


def test_disabled_runner_does_nothing():
    adapter = SimulatedAdapter()
    runner = GestureRunner(adapter, allowed=["saludar"], enabled=False)
    assert runner.request("saludar") is False
    assert adapter.performed == []


def test_gesture_runner_survives_adapter_errors():
    class Broken:
        def warm_up(self): ...
        def perform(self, gesture):
            raise RuntimeError("sdk caído")

    runner = GestureRunner(Broken(), allowed=["saludar"])
    assert runner.request("saludar") is True      # no lanza; el error se registra
