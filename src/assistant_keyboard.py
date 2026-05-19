import time

from src.assistant import answer_question


USE_VOICE = True


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

    while True:
        question = input("\nTú: ").strip()

        if not question:
            continue

        if question.lower() in ["salir", "exit", "q"]:
            print("ZUU: Hasta luego.")
            break

        start = time.time()

        answer = answer_question(question)

        elapsed = time.time() - start

        print(f"\nZUU: {answer}")
        print(f"Tiempo de respuesta: {elapsed:.2f} segundos")

        speak_answer(answer)


if __name__ == "__main__":
    main()