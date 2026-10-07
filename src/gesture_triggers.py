"""
Deteccion de frases que disparan gestos del robot (saludo, dar la mano,
saludo con la mano derecha) y la camara ("que ves"), compartido entre los
distintos front-ends del asistente (teclado, voz con microfono del robot,
etc.) para no duplicar esta logica en cada uno.

Los gestos corren en hilos daemon para no bloquear el asistente mientras
el robot se mueve. Si el proceso termina (Ctrl+C, "salir", o cualquier
excepcion) mientras un gesto sigue a medio camino, Python mataria el hilo
de golpe -- el brazo podria quedar colgado a medio movimiento, sin volver
a reposo ni soltar el control. Por eso se registran los hilos activos y,
al salir, wait_for_active_gestures() espera a que cada gesto en curso
termine su secuencia completa antes de cerrar. Cada front-end debe llamar
wait_for_active_gestures() en un finally alrededor de su loop principal.
"""

import os
import threading

from src.assistant import (
    is_only_greeting,
    is_handshake_request,
    is_right_hand_wave_request,
    is_vision_request,
    add_to_history,
    finish_answer,
)
from src.config import get_settings, get_base_dir

_gestures_settings = get_settings().get("gestures", {})
GESTURES_ENABLED = _gestures_settings.get("enabled", False)
GESTURES_NETWORK_INTERFACE = _gestures_settings.get("network_interface", "eth0")

RIGHT_HAND_WAVE_POSE_PATH = os.path.join(
    get_base_dir(), "..", "poses_guardadas", "saludo_mano_derecha.json"
)

_vision_settings = get_settings().get("vision", {})
VISION_ENABLED = _vision_settings.get("enabled", False)

GESTURE_EXIT_WAIT_TIMEOUT_SECONDS = 15.0

_active_gesture_threads = []
_active_gesture_threads_lock = threading.Lock()


def _start_gesture_thread(target):
    thread = threading.Thread(target=target, daemon=True)

    with _active_gesture_threads_lock:
        _active_gesture_threads.append(thread)

    thread.start()


def wait_for_active_gestures():
    with _active_gesture_threads_lock:
        threads = list(_active_gesture_threads)

    still_running = [t for t in threads if t.is_alive()]

    if not still_running:
        return

    print("\nEsperando a que el robot termine el gesto en curso antes de salir...")

    for thread in still_running:
        thread.join(timeout=GESTURE_EXIT_WAIT_TIMEOUT_SECONDS)


def try_warm_up_gestures():
    if not GESTURES_ENABLED:
        return

    try:
        from src.robot_gestures import warm_up as warm_up_gestures

        warm_up_gestures(network_interface=GESTURES_NETWORK_INTERFACE)

    except Exception as error:
        print(f"No se pudo precalentar los gestos: {error}")


def maybe_wave_greeting(question: str):
    if not GESTURES_ENABLED:
        return

    if not is_only_greeting(question):
        return

    def _run():
        try:
            from src.robot_gestures import wave_arm
            wave_arm(network_interface=GESTURES_NETWORK_INTERFACE)
        except Exception as error:
            print(f"No se pudo saludar con el brazo: {error}")

    _start_gesture_thread(_run)


def maybe_shake_hand(question: str):
    if not GESTURES_ENABLED:
        return

    if not is_handshake_request(question):
        return

    def _run():
        try:
            from src.robot_gestures import shake_hand
            shake_hand(network_interface=GESTURES_NETWORK_INTERFACE)
        except Exception as error:
            print(f"No se pudo dar la mano: {error}")

    _start_gesture_thread(_run)


def maybe_wave_right_hand(question: str):
    if not GESTURES_ENABLED:
        return

    if not is_right_hand_wave_request(question):
        return

    def _run():
        try:
            from src.robot_gestures import play_saved_pose
            play_saved_pose(RIGHT_HAND_WAVE_POSE_PATH, network_interface=GESTURES_NETWORK_INTERFACE)
        except Exception as error:
            print(f"No se pudo hacer el saludo con la mano derecha: {error}")

    _start_gesture_thread(_run)


def maybe_trigger_gestures(question: str):
    """Revisa una frase contra todos los gestos conocidos y dispara el
    que corresponda (puede que ninguno)."""
    maybe_wave_greeting(question)
    maybe_shake_hand(question)
    maybe_wave_right_hand(question)


def get_vision_answer(question: str):
    if not VISION_ENABLED:
        return None

    if not is_vision_request(question):
        return None

    add_to_history("user", question)

    try:
        from src.robot_vision import describe_what_i_see
        answer = describe_what_i_see()
    except Exception as error:
        answer = f"No pude usar la cámara en este momento: {error}"

    return finish_answer(answer)
