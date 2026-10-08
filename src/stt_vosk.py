from pathlib import Path
import json
import wave
import sqlite3
import unicodedata
import re

from vosk import Model, KaldiRecognizer

from src.config import get_settings, resolve_path


settings = get_settings()

VOSK_MODEL_PATH = resolve_path(settings["stt"]["vosk_model"])
SAMPLE_RATE = int(settings["stt"].get("sample_rate", 16000))
DB_PATH = resolve_path(settings["paths"]["database"])

_model = None
_grammar = None

# Con gramática cerrada la confianza suele ser alta incluso para ruido,
# así que conviene un umbral más exigente que 0.35. Ajustar probando en sitio.
MIN_CONFIDENCE = 0.6


BASE_GRAMMAR = [
    "zuu",
    "zu",
    "zú",
    "su",
    "suu",
    "zoo",
    "oye zuu",
    "hey zuu",
    "ok zuu",
    # Wake word compuesta para el microfono del robot: "zuu" sola se
    # confundia acusticamente con palabras cortas comunes (tu, si, su), y
    # "robot" sola genera falsos positivos en frases normales. Combinando
    # ambas ("robot su", "robot tu"...) se reduce falsos positivos y
    # negativos. "zuu", "zú" y "sú" no estan en el vocabulario del modelo
    # Vosk pequeno (vosk-model-small-es-0.42, ver .env), asi que no se usan
    # como variantes del segundo sonido.
    "robot",
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
    "hola suu",
    "hola su",
    "su",
    "siu",
    "suuu",
    "hola",
    "hola zuu",
    "buenas",
    "buenos dias",
    "buenos días",
    "buenas tardes",
    "buenas noches",
    "como estas",
    "cómo estás",
    "quien eres",
    "quién eres",
    "cuanto cuesta",
    "cuánto cuesta",
    "cuanto vale",
    "cuánto vale",
    "valor",
    "precio",
    "costo",
    "matricula",
    "matrícula",
    "cuanto dura",
    "cuánto dura",
    "duracion",
    "duración",
    "modalidad",
    "presencial",
    "virtual",
    "diurno",
    "diurna",
    "dia",
    "día",
    "noche",
    "nocturno",
    "nocturna",
    "jornada",
    "malla",
    "materias",
    "asignaturas",
    "plan de estudios",
    "campo laboral",
    "trabajo",
    "trabajar",
    "creditos",
    "créditos",
    "codigo snies",
    "código snies",
    "resolucion",
    "resolución",
    "registro calificado",
    "contacto",
    "telefono",
    "teléfono",
    "correo",
    "hablame sobre",
    "háblame sobre",
    "dime sobre",
    "cuentame sobre",
    "cuéntame sobre",
    "quiero saber sobre",
    "universidad",
    "udi",
    "universidad de investigacion y desarrollo",
    "universidad de investigación y desarrollo",
    "modo feliz",
    "modo alegre",
    "ponte feliz",
    "ponte serio",
    "modo serio",
    "modo chistoso",
    "ponte chistoso",
    "modo gracioso",
    "modo tranquilo",
    "modo emocionado",
    "ponte emocionado",
    "modo neutral",
    "sin emociones",
    "desactiva emociones",
    "como te sientes",
    "que sientes",
    "estado de animo",
    "riete",
    "ríete",
    "haz una risa",
    "ecopetrol",
    "eco petrol",
    "empresa ecopetrol",
    "visita a ecopetrol",
    "visita empresarial",
    "informacion de ecopetrol",
    "información de ecopetrol",
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
    "zuu espera",

    # NOTA IMPORTANTE: desde que se cambio al modelo Vosk pequeno
    # (vosk-model-small-es-0.42, ver .env) la gramatica cerrada SI se
    # aplica de verdad (antes, con el modelo grande, se ignoraba por
    # completo -- ver "Runtime graphs are not supported"). Eso significa
    # que cualquier palabra/frase que el codigo intenta reconocer por
    # texto (is_handshake_request, is_probably_udi_related, etc. en
    # assistant.py, o handle_fast_command en assistant_voice_zuu_micro.py)
    # pero que NO este aqui, es literalmente imposible que Vosk la
    # transcriba -- ya no hay reconocimiento libre de respaldo. Todo lo
    # de abajo se agrego para cerrar ese hueco tras detectar que preguntas
    # como "quien es el rector de la udi" o "dame la mano" no se
    # reconocian para nada con el modelo pequeno.

    # Rector / presidente / fundador / Jairo Castro
    "rector",
    "rectoria",
    "rectoría",
    "quien es el rector",
    "quién es el rector",
    "rector de la udi",
    "quien es el rector de la udi",
    "quién es el rector de la udi",
    "presidente",
    "presidencia",
    "presidente de la udi",
    "quien es el presidente",
    "quién es el presidente",
    "quien es el presidente de la udi",
    "quién es el presidente de la udi",
    "fundador",
    "fundadora",
    "quien fundo la udi",
    "quién fundó la udi",
    "quien es el fundador",
    "quién es el fundador",
    "dueño",
    "dueña",
    "quien es el dueño",
    "quién es el dueño",
    "quien es el dueño de la udi",
    "quién es el dueño de la udi",
    "jairo",
    "castro",
    "jairo castro",
    "jairo augusto castro castro",
    "quien es jairo castro",
    "quién es jairo castro",
    "vicerrector",
    "vicerrectora",
    "vicerrectoria",
    "vicerrectoría",
    "sala general",
    "autoridades",
    "directivos",
    "quien dirige",
    "quién dirige",
    "quien dirige la udi",
    "quién dirige la udi",
    "quien maneja",
    "quién maneja",
    "quien maneja la udi",
    "quién maneja la udi",
    "quien te creo",
    "quién te creó",
    "quien te hizo",
    "quién te hizo",
    "creador",
    "creado",
    "creada",
    "desarrollado",
    "desarrollaron",

    # Gestos: dar la mano
    "dame la mano",
    "dame tu mano",
    "dame la manito",
    "dame esa mano",
    "choca esa mano",
    "chocala",
    "chócala",
    "saludame de mano",
    "salúdame de mano",
    "estrechame la mano",
    "estréchame la mano",

    # Gestos: saludo con la mano derecha
    "saludar mano derecha",
    "saluda mano derecha",
    "saludar con la mano derecha",
    "saluda con la mano derecha",
    "saludar mano der",
    "saluda mano der",

    # Vision (camara)
    "que ves",
    "qué ves",
    "que vez",
    "qué vez",
    "que estas viendo",
    "qué estás viendo",
    "que puedes ver",
    "qué puedes ver",
    "que ve el robot",
    "qué ve el robot",
    "dime que ves",
    "dime qué ves",
    "cuentame que ves",
    "cuéntame qué ves",

    # Small talk / cortesia adicional
    "como vas",
    "cómo vas",
    "que haces",
    "qué haces",
    "como te llamas",
    "cómo te llamas",
    "hey",
    "holi",
    "que tal",
    "qué tal",
    "muchas gracias",
    "mil gracias",
    "te agradezco",
    "vale gracias",
    "ok gracias",
    "listo gracias",
    "perfecto gracias",
    "muy amable",

    # Comandos rapidos (handle_fast_command en assistant_voice_zuu_micro.py)
    "haz un chiste",
    "cuenta un chiste",
    "di un chiste",
    "es un chiste",
    "modo feria",
    "activa modo feria",
    "activar modo feria",
    "modo normal",
    "activa modo normal",
    "desactiva modo feria",
    "que modo estas usando",
    "en que modo estas",
    "modo actual",
    "calibra ruido",
    "calibrar ruido",
    "recalibra ruido",
    "ajusta microfono",
    "ajusta micrófono",
    "imita lo que yo diga",
    "repite lo que yo diga",
    "repite lo que diga",
    "modo loro",
    "modo imitacion",
    "modo imitación",
    "deja de imitar",
    "salir de modo imitacion",
    "salir de modo imitación",
    "desactiva modo loro",
    "para de repetir",
    "repite tu respuesta",
    "repite lo ultimo",
    "repite lo último",
    "otra vez",
]


EXTRA_PROGRAM_PHRASES = [
    "ingenieria de sistemas",
    "ingeniería de sistemas",
    "sistemas",
    "negocios internacionales",
    "negocio internacionales",
    "administracion de empresas",
    "administración de empresas",
    "comunicacion social",
    "comunicación social",
    "criminalistica",
    "criminalística",
    "derecho",
    "diseno grafico",
    "diseño gráfico",
    "diseno industrial",
    "diseño industrial",
    "ingenieria civil",
    "ingeniería civil",
    "ingenieria electronica",
    "ingeniería electrónica",
    "ingenieria industrial",
    "ingeniería industrial",
]


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(
        char for char in text
        if unicodedata.category(char) != "Mn"
    )


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def add_phrase(phrases: set[str], phrase: str):
    phrase = normalize_spaces(str(phrase).lower())

    if not phrase:
        return

    phrases.add(phrase)

    plain = normalize_spaces(strip_accents(phrase))

    if plain:
        phrases.add(plain)


def load_program_names_from_db() -> list[str]:
    if not DB_PATH.exists():
        return []

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT nombre, titulo FROM programs")
        rows = cur.fetchall()

        conn.close()

    except Exception:
        return []

    names = []

    for row in rows:
        if row["nombre"]:
            names.append(row["nombre"])

        if row["titulo"]:
            names.append(row["titulo"])

    return names


def build_grammar():
    global _grammar

    if _grammar is not None:
        return _grammar

    phrases = set()

    for phrase in BASE_GRAMMAR:
        add_phrase(phrases, phrase)

    for phrase in EXTRA_PROGRAM_PHRASES:
        add_phrase(phrases, phrase)

    for name in load_program_names_from_db():
        add_phrase(phrases, name)

        clean = normalize_spaces(name.lower())
        clean_no_accents = normalize_spaces(strip_accents(clean))

        # Agrega última palabra útil, por ejemplo: sistemas, derecho, industrial.
        for version in [clean, clean_no_accents]:
            parts = version.split()

            if parts:
                add_phrase(phrases, parts[-1])

            if len(parts) >= 2:
                add_phrase(phrases, " ".join(parts[-2:]))

    phrases.add("[unk]")

    _grammar = sorted(phrases)
    print(f"Gramática Vosk cargada con {len(_grammar)} frases.")

    return _grammar


def get_vosk_model():
    global _model

    if _model is None:
        if not VOSK_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"No encontré el modelo Vosk en: {VOSK_MODEL_PATH}"
            )

        print(f"Cargando modelo Vosk desde: {VOSK_MODEL_PATH}")
        _model = Model(str(VOSK_MODEL_PATH))
        print("Modelo Vosk cargado.")

    return _model


def get_average_confidence(result: dict) -> float:
    words = result.get("result", [])

    if not words:
        return 0.0

    confidences = [
        item.get("conf", 0.0)
        for item in words
        if "conf" in item
    ]

    if not confidences:
        return 0.0

    return sum(confidences) / len(confidences)


def transcribe_audio(audio_path: str | Path, min_confidence: float | None = None) -> str:
    audio_path = Path(audio_path)

    if not audio_path.exists():
        raise FileNotFoundError(f"No existe el audio: {audio_path}")

    model = get_vosk_model()
    grammar = json.dumps(build_grammar(), ensure_ascii=False)

    with wave.open(str(audio_path), "rb") as wav_file:
        if wav_file.getnchannels() != 1:
            raise ValueError("El audio debe estar en mono.")

        if wav_file.getframerate() != SAMPLE_RATE:
            raise ValueError(f"El audio debe estar a {SAMPLE_RATE} Hz.")

        recognizer = KaldiRecognizer(model, SAMPLE_RATE, grammar)
        recognizer.SetWords(True)

        while True:
            data = wav_file.readframes(4000)

            if len(data) == 0:
                break

            recognizer.AcceptWaveform(data)

        result = json.loads(recognizer.FinalResult())

    words = result.get("result", [])

    # Con gramática cerrada, Vosk marca como "[unk]" lo que no reconoce
    # (ruido, voces de fondo). Antes esos "[unk]" llegaban como texto y el
    # asistente respondía "Solo puedo ayudarte con información de la UDI".
    known_words = [
        item for item in words
        if item.get("word") and item.get("word") != "[unk]"
    ]

    if not known_words:
        return ""

    unk_count = len(words) - len(known_words)

    # Si la mayor parte del audio fue desconocida, es ruido/conversación de fondo.
    if unk_count > len(known_words):
        return ""

    confidence = sum(item.get("conf", 0.0) for item in known_words) / len(known_words)
    threshold = MIN_CONFIDENCE if min_confidence is None else min_confidence

    if confidence < threshold:
        return ""

    return " ".join(item["word"] for item in known_words).strip()


def warm_up_vosk():
    get_vosk_model()
    build_grammar()
    return "Vosk listo."