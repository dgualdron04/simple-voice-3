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


def transcribe_audio(audio_path: str | Path) -> str:
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

    text = result.get("text", "").strip()
    confidence = get_average_confidence(result)

    if confidence and confidence < 0.35:
        return ""

    return text


def warm_up_vosk():
    get_vosk_model()
    build_grammar()
    return "Vosk listo."