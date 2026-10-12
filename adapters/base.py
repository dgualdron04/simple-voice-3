"""Interfaz de gestos. El LLM/diálogo solo conoce nombres genéricos de la lista blanca."""
from __future__ import annotations

import threading
from typing import Protocol


class GestureAdapter(Protocol):
    def warm_up(self) -> None: ...
    def perform(self, gesture: str) -> bool: ...


class SimulatedAdapter:
    """Sin hardware: solo registra. Es el adapter por defecto y el de las pruebas."""

    def __init__(self):
        self.performed: list[str] = []

    def warm_up(self) -> None:
        pass

    def perform(self, gesture: str) -> bool:
        self.performed.append(gesture)
        print(f"[gesto simulado] {gesture}")
        return True


class GestureRunner:
    """Valida contra la lista blanca y ejecuta en un hilo, un gesto a la vez.

    Nunca bloquea la voz ni lanza excepciones al pipeline.
    """

    def __init__(self, adapter: GestureAdapter, allowed: list[str], enabled: bool = True):
        self.adapter = adapter
        self.allowed = set(allowed)
        self.enabled = enabled
        self._busy = threading.Lock()

    def request(self, gesture: str) -> bool:
        if not self.enabled or gesture not in self.allowed:
            return False
        if not self._busy.acquire(blocking=False):
            return False  # ya hay un gesto en curso

        def run():
            try:
                self.adapter.perform(gesture)
            except Exception as error:  # el robot nunca debe tumbar la conversación
                print(f"[gesto] error ejecutando '{gesture}': {error}")
            finally:
                self._busy.release()

        threading.Thread(target=run, daemon=True).start()
        return True


def build_gesture_runner(settings: dict) -> GestureRunner:
    cfg = settings["gestures"]
    name = cfg.get("adapter", "simulated")
    allowed = cfg.get("allowed", [])
    enabled = bool(cfg.get("enabled", True))

    if name == "g1" and enabled:
        try:
            from adapters.g1 import G1Adapter
            g1 = cfg.get("g1", {})
            adapter = G1Adapter(g1.get("network_interface", "eth0"), g1.get("poses_dir"))
            adapter.warm_up()
            return GestureRunner(adapter, allowed, True)
        except Exception as error:
            print(f"[gesto] G1 no disponible ({error}); gestos desactivados.")
            return GestureRunner(SimulatedAdapter(), allowed, False)

    if name != "simulated":
        print(f"[gesto] adapter '{name}' sin implementar; gestos desactivados.")
        return GestureRunner(SimulatedAdapter(), allowed, False)

    return GestureRunner(SimulatedAdapter(), allowed, enabled)
