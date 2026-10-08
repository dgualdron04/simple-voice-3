import time
import re
import unicodedata
import threading
import contextlib
import io
import sounddevice as sd

from src.robot_audio_recorder import (
    record_until_silence,
    calibrate_noise,
    start_microphone_session,
    stop_microphone_session,
)
from src.stt_vosk import transcribe_audio, warm_up_vosk
from src.stt_postprocess import fix_stt_text
from src.assistant import answer_question, set_assistant_mode, get_assistant_mode
from src.tts import warm_up_tts, speak
from src.llm import generate_voice_answer
from src.gesture_triggers import (
    GESTURES_NETWORK_INTERFACE,
    maybe_trigger_gestures,
    try_warm_up_gestures,
    wait_for_active_gestures,
)

# "zuu" sola se confundia acusticamente con palabras cortas comunes (tu, si,
# su) y casi nunca se reconocia. "robot" sola tambien genera falsos positivos
# (aparece en frases normales). Por eso ahora se exige la combinacion
# "robot" + sonido tipo "zuu" (con varias variantes de como Vosk puede
# transcribir ese segundo sonido) como palabra de activacion compuesta.
# Con el modelo Vosk pequeño (gramatica cerrada real, ver .env) las formas
# "zuu", "zú" y "sú" quedan fuera del vocabulario del modelo y Vosk nunca
# las puede producir como salida, asi que no sirven como variantes.
WAKE_WORDS = [
    "robot zu",
    "robot su",
    "robot suu",
    "robot zoo",
    "robot tu",
    "robot tú",
]

# La palabra de activación debe estar entre las primeras palabras de la frase.
WAKE_WORD_MAX_POSITION = 3

# Respuestas que indican que lo escuchado no era una pregunta útil.
REJECTION_ANSWERS = [
    "Solo puedo ayudarte con información relacionada",
    "No escuché una pregunta",
]

# Cuántas veces seguidas puede responder "fuera de tema" antes de volver a espera.
MAX_REJECTIONS_IN_A_ROW = 1

# Pausa tras hablar para que el micrófono no capte el final de la propia voz
# (el parlante del robot tiene algo de latencia y eco en la sala).
POST_SPEAK_PAUSE = 0.8

# Palabras sueltas que sí se aceptan en modo conversación.
SINGLE_WORD_COMMANDS = {
    "salir", "terminar", "adios", "gracias", "chiste", "hola",
    "para", "stop", "silencio", "pausa", "espera", "detente", "callate",
}

# El microfono del robot capta mas bajo que el local en el que se calibro
# el umbral de 0.6 (stt_vosk.MIN_CONFIDENCE). Con ese umbral, una palabra
# corta y no estandar como "zuu" dicha a volumen normal caia por debajo y
# Vosk la descartaba -- de ahi que "no se reconociera para nada".
ROBOT_MIC_MIN_CONFIDENCE = 0.4

BAD_WORDS = [
    "puta",
    "puto",
    "mierda",
    "marica",
    "gonorrea",
    "malparido",
    "hijueputa",
    "hp",
    "careverga",
    "verga",
    "pendejo",
    "idiota",
    "imbecil",
]

STOP_COMMANDS = [
    "para",
    "stop",
    "callate",
    "cállate",
    "silencio",
    "haz silencio",
    "espera",
    "detente",
    "pausa",

    "para zuu",
    "stop zuu",
    "callate zuu",
    "cállate zuu",
    "haz silencio zuu",
    "silencio zuu",
    "espera zuu",
    "detente zuu",
    "pausa zuu",

    "zuu para",
    "zuu stop",
    "zuu callate",
    "zuu cállate",
    "zuu haz silencio",
    "zuu espera",
]


def is_stop_command(text: str) -> bool:
    if not text:
        return False

    raw = normalize_for_wake(text)
    without_wake = normalize_for_wake(remove_wake_word(text))

    normalized_commands = {
        normalize_for_wake(command)
        for command in STOP_COMMANDS
    }

    return raw in normalized_commands or without_wake in normalized_commands


def watch_interrupt_command(finish_event, interrupted_event):
    while not finish_event.is_set():
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                heard_text = listen_once(
                    max_seconds=0.9,
                    min_seconds=0.2,
                    silence_seconds=0.25,
                    energy_threshold=voice_threshold,
                )

            if is_stop_command(heard_text):
                interrupted_event.set()
                finish_event.set()
                sd.stop()
                print("ZUU fue interrumpido. Esperando nueva instrucción.")
                return

        except Exception:
            time.sleep(0.1)


def speak_interruptible(text: str) -> bool:
    finish_event = threading.Event()
    interrupted_event = threading.Event()

    watcher = threading.Thread(
        target=watch_interrupt_command,
        args=(finish_event, interrupted_event),
        daemon=True
    )

    watcher.start()

    try:
        speak(text)
    finally:
        finish_event.set()
        watcher.join(timeout=0.3)

    return interrupted_event.is_set()

def censor_bad_words(text: str) -> str:
    words = text.split()
    clean_words = []

    for word in words:
        normalized = normalize_for_wake(word)

        if normalized in BAD_WORDS:
            clean_words.append("[palabra bloqueada]")
        else:
            clean_words.append(word)

    return " ".join(clean_words)

CONVERSATION_TIMEOUT = 25

last_answer = ""
mimic_mode = False
voice_threshold = 350


def generate_joke() -> str:
    prompt = """
        Eres ZUU, asistente virtual de la UDI.

        Cuenta un chiste corto, limpio y apto para todo público.
        Reglas:
        - No uses groserías.
        - No uses contenido ofensivo.
        - No uses temas sexuales, violentos ni discriminatorios.
        - Máximo dos frases.
        - Devuelve solo el chiste.

        Chiste:
        """

    answer = generate_voice_answer(
        prompt,
        max_tokens=45,
        temperature=0.8
    )

    answer = answer.strip()

    if not answer:
        return "No se me ocurrió un chiste en este momento."

    return answer

def try_warm_up_robot_speaker():
    # Sin esto, la primera vez que se usa el parlante del robot paga en
    # vivo el costo de conexion del AudioClient -- es justo lo que hacia
    # que la voz saliera notoriamente tarde la primera vez (mismo problema
    # que se arreglo antes en assistant_keyboard.py, nunca copiado aca).
    try:
        from src.robot_speaker import warm_up as warm_up_robot_speaker_connection

        warm_up_robot_speaker_connection(network_interface=GESTURES_NETWORK_INTERFACE)

    except Exception as error:
        print(f"No se pudo precalentar el parlante del robot: {error}")


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
    words = normalize_for_wake(text).split()
    wake_set = {normalize_for_wake(wake) for wake in WAKE_WORDS}
    max_wake_len = max((len(wake.split()) for wake in wake_set), default=1)

    # Ahora la palabra de activación es una frase compuesta ("robot su",
    # "robot tu"...), así que se busca por palabra completa (evita que "su"
    # coincida dentro de "resultado", "usuario", etc.) en cualquier posición
    # que empiece dentro de las primeras WAKE_WORD_MAX_POSITION palabras.
    for start in range(min(len(words), WAKE_WORD_MAX_POSITION)):
        for length in range(1, max_wake_len + 1):
            candidate = " ".join(words[start:start + length])

            if candidate in wake_set:
                return True

    return False


def is_rejection(answer: str) -> bool:
    return any(answer.startswith(prefix) for prefix in REJECTION_ANSWERS)


def looks_like_noise(text: str) -> bool:
    words = normalize_for_wake(text).split()

    if not words:
        return True

    # Una sola palabra suelta suele ser ruido que Vosk forzó a la gramática
    # ("dia", "valor", "hola"...). Solo se aceptan comandos conocidos.
    if len(words) == 1 and words[0] not in SINGLE_WORD_COMMANDS:
        return True

    return False


def remove_wake_word(text: str) -> str:
    original = text.strip()
    words = original.split()
    normalized_words = normalize_for_wake(original).split()

    # Usa la MISMA búsqueda que has_wake_word (cualquier posición dentro de
    # las primeras WAKE_WORD_MAX_POSITION palabras, no solo al inicio de la
    # frase). Antes, con "hola robot su algo", has_wake_word encontraba
    # "robot su" en la posición 1, pero esta función buscaba desde el
    # principio y cortaba por "hola robot" (que coincide primero en la
    # lista de abajo), dejando "su algo" en vez de "algo" como pregunta.
    wake_set = {normalize_for_wake(wake) for wake in WAKE_WORDS}
    max_wake_len = max((len(wake.split()) for wake in wake_set), default=1)

    for start in range(min(len(normalized_words), WAKE_WORD_MAX_POSITION)):
        for length in range(max_wake_len, 0, -1):
            candidate = " ".join(normalized_words[start:start + length])

            if candidate in wake_set:
                return " ".join(words[start + length:]).strip()

    normalized = normalize_for_wake(original)

    # Respaldo para formas antiguas ("zuu", "oye zuu"...) que ya no son
    # palabra de activación valida pero se siguen usando en STOP_COMMANDS.
    wake_phrases = [
        "robot zu",
        "robot su",
        "robot suu",
        "robot zoo",
        "robot tu",
        "robot tú",
        "oye robot",
        "hola robot",
        "hey robot",
        "ok robot",
        "robot",
        "oye zuu",
        "hola zuu",
        "hey zuu",
        "ok zuu",
        "zuu",
        "zu",
        "zú",
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

    raw_text = transcribe_audio(audio_path, min_confidence=ROBOT_MIC_MIN_CONFIDENCE)
    fixed_text = fix_stt_text(raw_text)

    print(f"Tú bruto: {raw_text}")
    print(f"Tú corregido: {fixed_text}")

    return fixed_text.strip()


def speak_fast(text: str, question: str = ""):
    global last_answer

    text = text.strip()

    if not text:
        return

    last_answer = text

    print(f"ZUU: {text}")
    #was_interrupted = speak_interruptible(text)

    speak(text)
    was_interrupted = False

    if not was_interrupted:
        time.sleep(POST_SPEAK_PAUSE)

def answer_and_speak(question: str, prefix: str = "", speak_rejection: bool = True):
    global last_answer

    start = time.time()

    answer = answer_question(question)

    if is_rejection(answer) and not speak_rejection:
        print(f"(Fuera de tema, se ignora en silencio): {question}")
        return answer

    if prefix:
        answer = f"{prefix}{answer}"

    last_answer = answer

    print(f"ZUU: {answer}")
    print(f"Tiempo respuesta: {time.time() - start:.2f} segundos")

    # was_interrupted = speak_interruptible(answer)

    speak(answer)
    was_interrupted = False

    if not was_interrupted:
        time.sleep(POST_SPEAK_PAUSE)

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

    if q in ["haz un chiste", "cuenta un chiste", "di un chiste", "chiste", "es un chiste"]:
        return generate_joke()

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
        return "Claro, dime la frase completa. Por ejemplo: robot su di esto en mandarín, hola cómo estás."

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
    print("ZUU - Asistente por voz (micrófono propio del G1)")
    print("Di 'robot su' (o robot zu, robot tu, robot zoo...) para activarme.")
    print("Ejemplos:")
    print("- robot su hola")
    print("- robot su háblame de ingeniería de sistemas")
    print("- robot su modo feria")
    print("- robot su haz un chiste")
    print("- robot su imita lo que yo diga")
    print("- robot su di esto en mandarín hola cómo estás")
    print("Para cerrar usa Ctrl + C")
    print("=" * 60)

    warm_up_vosk()
    try_warm_up_llm()
    warm_up_tts()
    try_warm_up_robot_speaker()
    try_warm_up_gestures()

    # Se prende UNA sola vez aca (dispara el aviso de "wake mode" del robot
    # una sola vez) y se apaga UNA sola vez al salir -- no en cada ciclo de
    # escucha, ver robot_audio_recorder.py.
    start_microphone_session()

    try:
        _run_loop()
    finally:
        # Nunca se cierra el proceso con un gesto todavia en curso (el
        # brazo quedaria colgado a medio movimiento) ni con el microfono
        # del robot encendido.
        wait_for_active_gestures()
        stop_microphone_session()


def _run_loop():
    global mimic_mode
    global voice_threshold

    voice_threshold = calibrate_noise()

    conversation_mode = False
    last_interaction = 0
    first_interaction = True
    rejections_in_a_row = 0

    while True:
        try:
            now = time.time()

            if conversation_mode and now - last_interaction > CONVERSATION_TIMEOUT:
                conversation_mode = False
                mimic_mode = False
                print("ZUU volvió a modo espera. Di 'robot su' para activarlo.")

            # Si el turno anterior disparo un gesto (saludo, dar la mano...),
            # se espera a que termine antes de volver a escuchar -- si no,
            # el microfono del robot puede captar el ruido/vibracion del
            # propio brazo moviendose como si fuera una instruccion nueva.
            wait_for_active_gestures()

            print("\nEscuchando...")

            # max_seconds y silence_seconds se subieron porque la grabacion
            # cortaba a mitad de frases largas (0.8s de silencio es muy poco
            # para una pausa natural al pensar la pregunta completa, y 4-6s
            # no alcanza para preguntas largas con nombre del programa, etc.)
            heard_text = listen_once(
                max_seconds=10 if conversation_mode else 7,
                min_seconds=0.6,
                silence_seconds=1.3,
                energy_threshold=voice_threshold,
            )

            if not heard_text:
                continue

            now = time.time()

            if not conversation_mode:
                if not has_wake_word(heard_text):
                    print("No escuché la palabra de activación. Ignorando...")
                    continue

                conversation_mode = True
                last_interaction = now
                rejections_in_a_row = 0

                question = remove_wake_word(heard_text)
                maybe_trigger_gestures(question)
                prefix = ""

                if first_interaction:
                    prefix = "Hola, soy Zú. "
                    first_interaction = False

                if is_stop_command(question):
                    mimic_mode = False
                    last_interaction = now
                    print("ZUU quedó en silencio. Esperando nueva instrucción.")
                    continue

                if not question:
                    speak_fast("Hola, soy Zú. Estoy listo para ayudarte con información de la UDI.", "hola")
                    continue

                fast_answer = handle_fast_command(question)

                if fast_answer:
                    if prefix:
                        fast_answer = f"{prefix}{fast_answer}"

                    speak_fast(fast_answer, question)
                    continue

                answer_and_speak(question)
                continue

            # Si ya está en conversación, no necesita decir ZUU otra vez.
            said_wake_word = has_wake_word(heard_text)
            question = remove_wake_word(heard_text)

            # Ruido o palabra suelta: se ignora sin hablar y SIN renovar el
            # tiempo de conversación, para que ZUU vuelva solo a modo espera.
            if not said_wake_word and looks_like_noise(question):
                print(f"Parece ruido, se ignora: {heard_text!r}")
                continue

            maybe_trigger_gestures(question)

            if normalize_for_wake(question) in ["salir", "terminar", "adios", "adiós"]:
                conversation_mode = False
                mimic_mode = False
                speak_fast("Modo conversación cerrado. Di robot su para activarme otra vez.", question)
                continue

            fast_answer = handle_fast_command(question)

            if fast_answer:
                speak_fast(fast_answer, question)
                last_interaction = now
                continue

            if mimic_mode:
                safe_text = censor_bad_words(question)

                if safe_text != question:
                    speak_fast("No puedo repetir esa palabra. Puedo repetir la frase sin groserías.", question)
                else:
                    speak_fast(safe_text, question)

                last_interaction = now
                continue

            # Solo dice "fuera de tema" si lo llamaron por su nombre o si aún no
            # lo ha dicho seguido; así no queda en bucle respondiéndole al ruido.
            allow_rejection = said_wake_word or rejections_in_a_row < MAX_REJECTIONS_IN_A_ROW
            answer = answer_and_speak(question, speak_rejection=allow_rejection)

            if is_rejection(answer):
                # Si lo llamaron por su nombre, el rechazo se dijo y no cuenta
                # para volver a espera.
                rejections_in_a_row = 0 if said_wake_word else rejections_in_a_row + 1

                if rejections_in_a_row > MAX_REJECTIONS_IN_A_ROW:
                    conversation_mode = False
                    mimic_mode = False
                    rejections_in_a_row = 0
                    print("Demasiadas entradas fuera de tema. ZUU vuelve a modo espera.")

                # No se renueva last_interaction: el ruido no mantiene viva la conversación.
                continue

            rejections_in_a_row = 0
            last_interaction = now

        except KeyboardInterrupt:
            print("\nZUU: Hasta luego.")
            break

        except Exception as error:
            print(f"Error en el ciclo de voz: {error}")
            time.sleep(1)


if __name__ == "__main__":
    main()