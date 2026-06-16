import time
import re
import unicodedata
import random

from src.audio_recorder import record_until_silence, calibrate_noise
from src.stt_vosk import transcribe_audio, warm_up_vosk
from src.stt_postprocess import fix_stt_text
from src.assistant import answer_question, set_assistant_mode, get_assistant_mode
from src.tts import warm_up_tts, speak
from src.llm import generate_voice_answer

from src.emotion_engine import (
    decorate_answer,
    handle_emotion_command,
    get_emotion_name,
)

WAKE_WORDS = [
    "zuu",
    "zu",
    "zú",
    "su",
    "suu",
    "zoo",
    "oye zuu",
    "hola zuu",
]

CONVERSATION_TIMEOUT = 25

last_answer = ""
mimic_mode = False
voice_threshold = 350


JOKES = [
    "¿Por qué el computador fue al médico? Porque tenía un virus.",
    "¿Qué le dijo un bit al otro bit? Nos vemos en el bus.",
    "¿Por qué Python no se estresa? Porque siempre intenta exceptuar sus problemas.",
    "¿Qué hace una abeja en el gimnasio? Zum ba.",
    "¿Por qué el programador confundió Halloween con Navidad? Porque OCT treinta y uno es igual a DEC veinticinco.",
]


def try_warm_up_llm():
    try:
        from src.llm import warm_up_llm

        print("Cargando LLM...")
        print(warm_up_llm())

    except Exception as error:
        print(f"No se pudo cargar LLM: {error}")


def normalize_for_wake(text: str) -> str:
    text = text.lower().strip()

    text = unicodedata.normalize("NFD", text)
    text = "".join(
        char for char in text
        if unicodedata.category(char) != "Mn"
    )

    text = re.sub(r"[^a-z0-9ñ\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def has_wake_word(text: str) -> bool:
    text = normalize_for_wake(text)

    return any(wake in text for wake in WAKE_WORDS)


def remove_wake_word(text: str) -> str:
    original = text.strip()
    normalized = normalize_for_wake(original)

    wake_phrases = [
        "oye zuu",
        "hola zuu",
        "hey zuu",
        "ok zuu",
        "zuu",
        "zu",
        "zú",
        "su",
        "suu",
        "zoo",
    ]

    for wake in wake_phrases:
        wake_normalized = normalize_for_wake(wake)

        if normalized.startswith(wake_normalized):
            words = original.split()
            wake_words_count = len(wake.split())
            return " ".join(words[wake_words_count:]).strip()

    return original


def text_after_prefix(text: str, prefixes: list[str]) -> str:
    normalized = normalize_for_wake(text)
    words = text.split()

    prefixes = sorted(prefixes, key=lambda item: len(item.split()), reverse=True)

    for prefix in prefixes:
        prefix_normalized = normalize_for_wake(prefix)

        if normalized.startswith(prefix_normalized):
            prefix_words_count = len(prefix.split())
            result = " ".join(words[prefix_words_count:]).strip()
            result = result.strip(":-,.; ")
            return result

    return ""


def listen_once(
    max_seconds=6,
    min_seconds=0.6,
    silence_seconds=0.8,
    energy_threshold=350
):
    audio_path = record_until_silence(
        max_seconds=max_seconds,
        min_seconds=min_seconds,
        silence_seconds=silence_seconds,
        energy_threshold=energy_threshold,
    )

    if not audio_path:
        return ""

    raw_text = transcribe_audio(audio_path)
    fixed_text = fix_stt_text(raw_text)

    print(f"Tú bruto: {raw_text}")
    print(f"Tú corregido: {fixed_text}")

    return fixed_text.strip()


def speak_fast(text: str, question: str = ""):
    global last_answer

    text = text.strip()

    if not text:
        return

    text = decorate_answer(
        question=question,
        answer=text,
        assistant_mode=get_assistant_mode()
    )

    last_answer = text

    print(f"ZUU [{get_emotion_name()}]: {text}")
    speak(text)

    # Evita que el micrófono capture la voz de ZUU.
    time.sleep(0.3)


def answer_and_speak(question: str):
    global last_answer

    start = time.time()

    answer = answer_question(question)

    answer = decorate_answer(
        question=question,
        answer=answer,
        assistant_mode=get_assistant_mode()
    )

    last_answer = answer

    print(f"ZUU [{get_emotion_name()}]: {answer}")
    print(f"Tiempo respuesta: {time.time() - start:.2f} segundos")

    speak(answer)

    # Evita que el micrófono capture la voz de ZUU.
    time.sleep(0.3)

    return answer


def translate_to_mandarin(text: str) -> str:
    if not text:
        return "Claro, dime qué frase quieres decir en mandarín."

    prompt = f"""
Eres ZUU.

Traduce la siguiente frase al chino mandarín.
Como la voz actual es española, responde en pinyin fácil de pronunciar.
No expliques nada.
No uses caracteres chinos.
Devuelve solo la frase en pinyin.

Frase:
{text}

Mandarín en pinyin:
"""

    answer = generate_voice_answer(
        prompt,
        max_tokens=50,
        temperature=0.2
    )

    answer = answer.strip()

    if not answer:
        return "No pude traducirlo en este momento."

    return answer


def handle_fast_command(question: str):
    global mimic_mode
    global voice_threshold
    global last_answer

    q = normalize_for_wake(question)

    emotion_answer = handle_emotion_command(question)

    if emotion_answer:
        return emotion_answer

    # Modo feria
    if q in ["modo feria", "activa modo feria", "activar modo feria"]:
        set_assistant_mode("feria")
        return "Modo feria activado. Responderé más breve y directo para visitantes."

    if q in ["modo normal", "activa modo normal", "desactiva modo feria"]:
        set_assistant_mode("normal")
        return "Modo normal activado."

    if q in ["que modo estas usando", "en que modo estas", "modo actual"]:
        mode = get_assistant_mode()
        return f"Estoy en modo {mode}."

    # Recalibrar ruido
    if q in ["calibra ruido", "calibrar ruido", "recalibra ruido", "ajusta microfono", "ajusta micrófono"]:
        voice_threshold = calibrate_noise()
        return f"Listo, ajusté el micrófono con umbral {voice_threshold:.0f}."

    # Modo imitación
    if q in [
        "imita lo que yo diga",
        "repite lo que yo diga",
        "repite lo que diga",
        "modo loro",
        "modo imitacion",
        "modo imitación",
    ]:
        mimic_mode = True
        return "Modo imitación activado. Di algo y yo lo repito."

    if q in [
        "deja de imitar",
        "salir de modo imitacion",
        "salir de modo imitación",
        "desactiva modo loro",
        "para de repetir",
    ]:
        mimic_mode = False
        return "Modo imitación desactivado."

    # Chistes rápidos sin LLM
    if q in ["haz un chiste", "cuenta un chiste", "di un chiste", "chiste"]:
        return f"Jajaja, {random.choice(JOKES)}"

    # Mandarín
    mandarin_text = text_after_prefix(question, [
        "di esto en mandarin",
        "di esto en mandarín",
        "di en mandarin",
        "di en mandarín",
        "dilo en mandarin",
        "dilo en mandarín",
        "traduce al mandarin",
        "traduce al mandarín",
    ])

    if mandarin_text:
        return translate_to_mandarin(mandarin_text)

    if "mandarin" in q or "mandarin" in q or "mandarín" in question.lower():
        return "Claro, dime la frase completa. Por ejemplo: ZUU di esto en mandarín, hola cómo estás."

    # Repetir algo directo
    repeat_text = text_after_prefix(question, [
        "repite esto",
        "repite",
        "di esto",
        "di",
    ])

    if repeat_text:
        return repeat_text

    # Repetir última respuesta
    if q in ["repite tu respuesta", "repite lo ultimo", "repite lo último", "otra vez"]:
        if last_answer:
            return last_answer

        return "Todavía no tengo una respuesta anterior para repetir."

    return None


def main():
    global mimic_mode
    global voice_threshold

    print("=" * 60)
    print("ZUU - Asistente por voz siempre activo")
    print("Di 'ZUU' para activarme.")
    print("Ejemplos:")
    print("- ZUU hola")
    print("- ZUU háblame de ingeniería de sistemas")
    print("- ZUU modo feria")
    print("- ZUU haz un chiste")
    print("- ZUU imita lo que yo diga")
    print("- ZUU di esto en mandarín hola cómo estás")
    print("Para cerrar usa Ctrl + C")
    print("=" * 60)

    warm_up_vosk()
    try_warm_up_llm()
    warm_up_tts()

    voice_threshold = calibrate_noise()

    conversation_mode = False
    last_interaction = 0

    while True:
        try:
            now = time.time()

            if conversation_mode and now - last_interaction > CONVERSATION_TIMEOUT:
                conversation_mode = False
                mimic_mode = False
                print("ZUU volvió a modo espera. Di 'ZUU' para activarlo.")

            print("\nEscuchando...")

            heard_text = listen_once(
                max_seconds=6 if conversation_mode else 4,
                min_seconds=0.6,
                silence_seconds=0.8,
                energy_threshold=voice_threshold,
            )

            if not heard_text:
                continue

            now = time.time()

            if not conversation_mode:
                if not has_wake_word(heard_text):
                    print("No escuché la palabra ZUU. Ignorando...")
                    continue

                conversation_mode = True
                last_interaction = now

                question = remove_wake_word(heard_text)

                if not question:
                    answer_and_speak("hola")
                    continue

                fast_answer = handle_fast_command(question)

                if fast_answer:
                    speak_fast(fast_answer, question)
                    continue

                answer_and_speak(question)
                continue

            # Si ya está en conversación, no necesita decir ZUU otra vez.
            question = remove_wake_word(heard_text)

            if normalize_for_wake(question) in ["salir", "terminar", "adios", "adiós"]:
                conversation_mode = False
                mimic_mode = False
                speak_fast("Modo conversación cerrado. Di ZUU para activarme otra vez.", question)
                continue

            fast_answer = handle_fast_command(question)

            if fast_answer:
                speak_fast(fast_answer, question)
                last_interaction = now
                continue

            if mimic_mode:
                speak_fast(question)
                last_interaction = now
                continue

            answer_and_speak(question)
            last_interaction = now

        except KeyboardInterrupt:
            print("\nZUU: Hasta luego.")
            break

        except Exception as error:
            print(f"Error en el ciclo de voz: {error}")
            time.sleep(1)


if __name__ == "__main__":
    main()