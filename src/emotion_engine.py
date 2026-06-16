import random
import re
import unicodedata


emotion_state = {
    "mood": "alegre",
    "energy": 0.7,
}


def normalize_text(text: str) -> str:
    text = text.lower().strip()

    text = unicodedata.normalize("NFD", text)
    text = "".join(
        char for char in text
        if unicodedata.category(char) != "Mn"
    )

    text = re.sub(r"[^a-z0-9ñ\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def get_emotion_name() -> str:
    return emotion_state["mood"]


def set_emotion(mood: str):
    mood = normalize_text(mood)

    valid_moods = [
        "alegre",
        "serio",
        "chistoso",
        "emocionado",
        "tranquilo",
        "empatico",
        "neutral",
    ]

    if mood not in valid_moods:
        mood = "alegre"

    emotion_state["mood"] = mood


def user_is_laughing(text: str) -> bool:
    q = text.lower()

    laugh_patterns = [
        "jaja",
        "jajaj",
        "jajaja",
        "jeje",
        "jejeje",
        "xd",
        "xD",
        "😂",
        "🤣",
        "me dio risa",
        "que risa",
        "qué risa",
    ]

    return any(pattern.lower() in q for pattern in laugh_patterns)


def detect_user_emotion(text: str):
    q = normalize_text(text)

    if user_is_laughing(text):
        set_emotion("chistoso")
        return

    happy_words = [
        "estoy bien",
        "muy bien",
        "feliz",
        "contento",
        "emocionado",
        "genial",
        "excelente",
        "brutal",
        "chimba",
    ]

    tired_words = [
        "cansado",
        "estresado",
        "aburrido",
        "preocupado",
        "me fue mal",
        "estoy mal",
        "triste",
    ]

    serious_words = [
        "serio",
        "rapido",
        "rápido",
        "directo",
        "sin chistes",
        "sin molestar",
    ]

    if any(word in q for word in happy_words):
        set_emotion("emocionado")
        return

    if any(word in q for word in tired_words):
        set_emotion("empatico")
        return

    if any(word in q for word in serious_words):
        set_emotion("serio")
        return


def handle_emotion_command(text: str):
    q = normalize_text(text)

    if q in [
        "como te sientes",
        "como estas emocionalmente",
        "que sientes",
        "cual es tu estado de animo",
        "estado de animo",
    ]:
        mood = get_emotion_name()

        return f"Me siento en modo {mood}. No tengo sentimientos reales, pero puedo simular emociones para conversar más natural."

    if q in [
        "ponte feliz",
        "modo feliz",
        "modo alegre",
        "activa modo alegre",
    ]:
        set_emotion("alegre")
        return "Modo alegre activado. Hoy vengo con buena energía."

    if q in [
        "ponte serio",
        "modo serio",
        "activa modo serio",
    ]:
        set_emotion("serio")
        return "Modo serio activado. Responderé más directo."

    if q in [
        "modo chistoso",
        "ponte chistoso",
        "modo gracioso",
        "activa modo chistoso",
    ]:
        set_emotion("chistoso")
        return "Jajaja, modo chistoso activado."

    if q in [
        "modo tranquilo",
        "ponte tranquilo",
        "habla tranquilo",
    ]:
        set_emotion("tranquilo")
        return "Modo tranquilo activado. Hablaré con más calma."

    if q in [
        "modo emocionado",
        "ponte emocionado",
        "activa modo emocionado",
    ]:
        set_emotion("emocionado")
        return "¡Modo emocionado activado! Me gusta esa energía."

    if q in [
        "modo neutral",
        "sin emociones",
        "desactiva emociones",
        "modo neutro",
    ]:
        set_emotion("neutral")
        return "Modo neutral activado."

    if q in [
        "riete",
        "ríete",
        "rie",
        "ríe",
        "haz una risa",
        "risa",
    ]:
        set_emotion("chistoso")
        return random.choice([
            "Jajaja.",
            "Jajaja, buena esa.",
            "Jejeje, me dio risa.",
            "Jajaja, casi me desconecto de la risa.",
        ])

    return None


def answer_should_stay_formal(question: str, answer: str, assistant_mode: str = "normal") -> bool:
    q = normalize_text(question)
    a = normalize_text(answer)

    if assistant_mode == "feria":
        return True

    formal_answer_signals = [
        "el valor de",
        "la modalidad de",
        "tiene una duracion",
        "tiene una duración",
        "el codigo snies",
        "el código snies",
        "el contacto de",
        "la jornada de",
        "no encontre",
        "no encontré",
        "solo puedo ayudarte",
    ]

    formal_question_signals = [
        "cuanto cuesta",
        "cuánto cuesta",
        "valor",
        "precio",
        "costo",
        "duracion",
        "duración",
        "creditos",
        "créditos",
        "snies",
        "modalidad",
        "malla",
        "contacto",
        "telefono",
        "teléfono",
        "correo",
        "jornada",
        "udi",
        "universidad",
    ]

    if any(signal in a for signal in formal_answer_signals):
        return True

    if any(signal in q for signal in formal_question_signals):
        return True

    return False


def decorate_answer(question: str, answer: str, assistant_mode: str = "normal") -> str:
    if not answer:
        return answer

    detect_user_emotion(question)

    mood = get_emotion_name()

    if answer_should_stay_formal(question, answer, assistant_mode):
        if user_is_laughing(question):
            return f"Jajaja, claro. {answer}"

        return answer

    if "no encontré" in answer.lower() or "solo puedo ayudarte" in answer.lower():
        return answer

    if user_is_laughing(question):
        return random.choice([
            f"Jajaja, {answer}",
            f"Jajaja, buena esa. {answer}",
            f"Jejeje, {answer}",
        ])

    if mood == "alegre":
        prefixes = [
            "¡De una!",
            "Listo.",
            "Claro que sí.",
        ]
        return f"{random.choice(prefixes)} {answer}"

    if mood == "emocionado":
        prefixes = [
            "¡Uy, me gusta esa energía!",
            "¡Eso suena genial!",
            "¡Vamos con toda!",
        ]
        return f"{random.choice(prefixes)} {answer}"

    if mood == "chistoso":
        prefixes = [
            "Jajaja,",
            "Jejeje,",
            "Buena esa,",
        ]
        return f"{random.choice(prefixes)} {answer}"

    if mood == "tranquilo":
        prefixes = [
            "Con calma.",
            "Tranquilo.",
            "Claro.",
        ]
        return f"{random.choice(prefixes)} {answer}"

    if mood == "empatico":
        prefixes = [
            "Te entiendo.",
            "Tranqui.",
            "Estoy contigo.",
        ]
        return f"{random.choice(prefixes)} {answer}"

    if mood == "serio":
        return answer

    if mood == "neutral":
        return answer

    return answer