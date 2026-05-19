import time

from src.audio_recorder import record_seconds
from src.whisper_stt import transcribe_audio, warm_up_whisper
from src.assistant import answer_question
from src.tts import warm_up_tts, speak


def try_warm_up_llm():
    try:
        from src.llm import warm_up_llm

        print("Cargando LLM...")
        print(warm_up_llm())

    except Exception as error:
        print(f"No se pudo cargar LLM: {error}")


def main():
    print("=" * 60)
    print("ZUU - Asistente por voz")
    print("Presiona Enter para hablar.")
    print("Escribe salir para cerrar.")
    print("=" * 60)

    warm_up_whisper()
    try_warm_up_llm()
    warm_up_tts()

    while True:
        command = input("\nPresiona Enter para grabar o escribe salir: ").strip().lower()

        if command in ["salir", "exit", "q"]:
            print("ZUU: Hasta luego.")
            break

        total_start = time.time()

        audio_path = record_seconds(4)

        stt_start = time.time()
        question = transcribe_audio(audio_path)
        stt_time = time.time() - stt_start

        print(f"Tú: {question}")
        print(f"Tiempo STT: {stt_time:.2f} segundos")

        if not question:
            print("ZUU: No escuché nada claro.")
            continue

        answer_start = time.time()
        answer = answer_question(question)
        answer_time = time.time() - answer_start

        print(f"ZUU: {answer}")
        print(f"Tiempo respuesta: {answer_time:.2f} segundos")

        speak(answer)

        print(f"Tiempo total: {time.time() - total_start:.2f} segundos")


if __name__ == "__main__":
    main()