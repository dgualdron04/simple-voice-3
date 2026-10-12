import threading
import time

import os

from src.assistant import (
    answer_question,
    is_only_greeting,
    is_handshake_request,
    is_right_hand_wave_request,
    is_vision_request,
    add_to_history,
    finish_answer,
)
from src.config import get_settings, get_base_dir


USE_VOICE = True

_gestures_settings = get_settings().get("gestures", {})
GESTURES_ENABLED = _gestures_settings.get("enabled", False)
GESTURES_NETWORK_INTERFACE = _gestures_settings.get("network_interface", "eth0")

RIGHT_HAND_WAVE_POSE_PATH = os.path.join(
    get_base_dir(), "..", "poses_guardadas", "saludo_mano_derecha.json"
)

_vision_settings = get_settings().get("vision", {})
VISION_ENABLED = _vision_settings.get("enabled", False)

# Los gestos corren en hilos daemon para no bloquear el chat mientras se
# mueven. Si el proceso termina (p. ej. al escribir "salir") mientras un
# gesto sigue a medio camino, Python mata el hilo de golpe -- el brazo puede
# quedar colgado a medio movimiento, sin volver a reposo ni soltar el
# control. Por eso se registran los hilos activos y, al salir, se espera a
# que cada gesto en curso termine su secuencia completa antes de cerrar.
_active_gesture_threads = []
_active_gesture_threads_lock = threading.Lock()

GESTURE_EXIT_WAIT_TIMEOUT_SECONDS = 15.0


def _start_gesture_thread(target):
    thread = threading.Thread(target=target, daemon=True)

    with _active_gesture_threads_lock:
        _active_gesture_threads.append(thread)

    thread.start()


def _wait_for_active_gestures():
    with _active_gesture_threads_lock:
        threads = list(_active_gesture_threads)

    still_running = [t for t in threads if t.is_alive()]

    if not still_running:
        return

    print("\nEsperando a que el robot termine el gesto en curso antes de salir...")

    for thread in still_running:
        thread.join(timeout=GESTURE_EXIT_WAIT_TIMEOUT_SECONDS)


def try_warm_up_llm():
    try:
        from src.llm import warm_up_llm

        print("Cargando modelo LLM...")
        start = time.time()
        result = warm_up_llm()
        print(f"LLM listo en {time.time() - start:.2f} segundos. Respuesta: {result}")

    except Exception as error:
        print(f"No se pudo calentar el LLM: {error}")


def try_warm_up_tts():
    if not USE_VOICE:
        return

    try:
        from src.tts import warm_up_tts

        print("Cargando voz Piper...")
        warm_up_tts()

    except Exception as error:
        print(f"No se pudo cargar Piper TTS: {error}")

    try:
        from src.robot_speaker import warm_up as warm_up_robot_speaker

        warm_up_robot_speaker(network_interface=GESTURES_NETWORK_INTERFACE)

    except Exception as error:
        print(f"No se pudo precalentar el parlante del robot: {error}")


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


def speak_answer(text: str):
    if not USE_VOICE:
        return

    try:
        from src.tts import speak
        speak(text)

    except Exception as error:
        print(f"No se pudo reproducir la voz: {error}")


def main():
    print("=" * 60)
    print("ZUU - Asistente por teclado")
    print("Puedes saludar, conversar o preguntar información de la UDI.")
    print("Para salir escribe: salir")
    print("=" * 60)

    try_warm_up_llm()
    try_warm_up_tts()
    try_warm_up_gestures()

    try:
        _main_loop()
    finally:
        # Cubre tanto la salida normal ("salir") como Ctrl+C o cualquier
        # excepcion a medio gesto: nunca se cierra el proceso con un gesto
        # todavia en curso (el brazo quedaria colgado a medio movimiento).
        _wait_for_active_gestures()


def _main_loop():
    while True:
        question = input("\nTú: ").strip()

        if not question:
            continue

        if question.lower() in ["salir", "exit", "q"]:
            print("ZUU: Hasta luego.")
            break

        maybe_wave_greeting(question)
        maybe_shake_hand(question)
        maybe_wave_right_hand(question)

        start = time.time()

        vision_answer = get_vision_answer(question)
        answer = vision_answer if vision_answer is not None else answer_question(question)

        elapsed = time.time() - start

        print(f"\nZUU: {answer}")
        print(f"Tiempo de respuesta: {elapsed:.2f} segundos")

        speak_answer(answer)


if __name__ == "__main__":
    main()