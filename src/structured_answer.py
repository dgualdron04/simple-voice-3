import sqlite3
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher
from src.config import get_settings


settings = get_settings()
DB_PATH = Path(settings["paths"]["database"])


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return text


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def get_connection():
    return sqlite3.connect(DB_PATH)


def get_all_programs():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM programs")
    rows = cur.fetchall()

    conn.close()
    return rows


def detect_program(question: str):
    programs = get_all_programs()

    best_program = None
    best_score = 0

    q = normalize(question)

    for program in programs:
        nombre = program["nombre"] or ""
        titulo = program["titulo"] or ""

        candidates = [nombre, titulo]

        for candidate in candidates:
            if not candidate:
                continue

            candidate_norm = normalize(candidate)

            if candidate_norm in q:
                return program

            score = similarity(q, candidate_norm)

            if score > best_score:
                best_score = score
                best_program = program

    if best_score >= 0.35:
        return best_program

    return None


def detect_intent(question: str):
    q = normalize(question)

    if any(word in q for word in ["cuanto cuesta", "valor", "precio", "costo", "matricula"]):
        if any(word in q for word in ["nocturna", "noche", "nocturno"]):
            return "valor_nocturna"

        if any(word in q for word in ["diurna", "dia", "diurno"]):
            return "valor_diurna"

        return "valor"

    if any(word in q for word in ["cuanto dura", "duracion", "semestres"]):
        return "duracion"

    if any(word in q for word in ["modalidad", "presencial", "virtual"]):
        return "modalidad"

    if any(word in q for word in ["sede", "campus", "ciudad"]):
        return "sede"

    if any(word in q for word in ["malla", "materias", "plan de estudios", "asignaturas"]):
        return "malla"

    if any(word in q for word in ["trabajo", "campo laboral", "en que puedo trabajar", "perfil ocupacional"]):
        return "trabajo"

    return "rag"


def format_money(value: str):
    if not value:
        return ""

    digits = "".join(char for char in value if char.isdigit())

    if not digits:
        return value

    number = int(digits)
    return f"${number:,.0f}".replace(",", ".")


def answer_exact(question: str):
    program = detect_program(question)

    if program is None:
        return None

    intent = detect_intent(question)
    nombre = program["nombre"]

    if intent == "valor_diurna":
        value = program["valor_diurna"]

        if value:
            return f"El valor de {nombre} en jornada diurna es de {format_money(value)}."

    if intent == "valor_nocturna":
        value = program["valor_nocturna"]

        if value:
            return f"El valor de {nombre} en jornada nocturna es de {format_money(value)}."

    if intent == "valor":
        diurna = program["valor_diurna"]
        nocturna = program["valor_nocturna"]

        if diurna and nocturna:
            return (
                f"El valor de {nombre} es: "
                f"diurna {format_money(diurna)} y nocturna {format_money(nocturna)}."
            )

        if diurna:
            return f"El valor de {nombre} es de {format_money(diurna)}."

        if nocturna:
            return f"El valor de {nombre} es de {format_money(nocturna)}."

    if intent == "duracion" and program["duracion"]:
        return f"{nombre} tiene una duración de {program['duracion']}."

    if intent == "modalidad" and program["modalidad"]:
        return f"La modalidad de {nombre} es {program['modalidad']}."

    if intent == "sede" and program["sede"]:
        return f"{nombre} se ofrece en la sede {program['sede']}."

    if intent == "malla" and program["malla"]:
        return f"La malla curricular de {nombre} es: {program['malla']}"

    if intent == "trabajo" and program["trabajo"]:
        return f"El campo laboral de {nombre} es: {program['trabajo']}"

    return None


def search_context(question: str, limit: int = 3):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    words = [
        normalize(word)
        for word in question.split()
        if len(word) >= 4
    ]

    if not words:
        return ""

    fts_query = " OR ".join(words)

    try:
        cur.execute("""
        SELECT title, content
        FROM documents_fts
        WHERE documents_fts MATCH ?
        LIMIT ?
        """, (fts_query, limit))

        rows = cur.fetchall()

    except sqlite3.OperationalError:
        rows = []

    conn.close()

    if not rows:
        return ""

    context_parts = []

    for row in rows:
        context_parts.append(
            f"Título: {row['title']}\nContenido:\n{row['content']}"
        )

    return "\n\n---\n\n".join(context_parts)

def get_fact_payload(question: str):
    program = detect_program(question)

    if program is None:
        return None

    intent = detect_intent(question)
    nombre = program["nombre"]

    if intent == "valor_diurna" and program["valor_diurna"]:
        return {
            "tipo": "valor_diurna",
            "programa": nombre,
            "jornada": "diurna",
            "valor": format_money(program["valor_diurna"])
        }

    if intent == "valor_nocturna" and program["valor_nocturna"]:
        return {
            "tipo": "valor_nocturna",
            "programa": nombre,
            "jornada": "nocturna",
            "valor": format_money(program["valor_nocturna"])
        }

    if intent == "valor":
        data = {
            "tipo": "valor",
            "programa": nombre
        }

        if program["valor_diurna"]:
            data["valor_diurna"] = format_money(program["valor_diurna"])

        if program["valor_nocturna"]:
            data["valor_nocturna"] = format_money(program["valor_nocturna"])

        if len(data) > 2:
            return data

    if intent == "duracion" and program["duracion"]:
        return {
            "tipo": "duracion",
            "programa": nombre,
            "duracion": program["duracion"]
        }

    if intent == "modalidad" and program["modalidad"]:
        return {
            "tipo": "modalidad",
            "programa": nombre,
            "modalidad": program["modalidad"]
        }

    if intent == "sede" and program["sede"]:
        return {
            "tipo": "sede",
            "programa": nombre,
            "sede": program["sede"]
        }

    if intent == "malla" and program["malla"]:
        return {
            "tipo": "malla",
            "programa": nombre,
            "malla": program["malla"]
        }

    if intent == "trabajo" and program["trabajo"]:
        return {
            "tipo": "trabajo",
            "programa": nombre,
            "trabajo": program["trabajo"]
        }

    return None