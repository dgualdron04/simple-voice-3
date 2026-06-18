import re
import sqlite3
import unicodedata
from pathlib import Path
from difflib import get_close_matches

from src.config import get_settings, resolve_path


settings = get_settings()
DB_PATH = resolve_path(settings["paths"]["database"])


STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "y", "o", "en", "por", "para", "con", "a", "que", "cual", "cuál",
    "cuanto", "cuánto", "cuesta", "vale", "valor", "precio", "programa",
    "carrera", "estudiar", "estudio", "quiero", "saber", "informacion",
    "información", "udi", "universidad", "hablame", "háblame", "sobre",
    "dime", "cuentame", "cuéntame", "ingenieria", "ingeniería"
}


MANUAL_ALIASES = {
    "administración de empresas": [
        "administracion de empresas",
        "administración empresas",
        "administracion empresas",
        "administracion",
        "administración",
        "admin empresas",
        "empresas",
    ],
    "comunicación social": [
        "comunicacion social",
        "comunicación",
        "comunicacion",
        "social",
    ],
    "criminalística": [
        "criminalistica",
        "criminalística",
        "criminalistica profesional",
    ],
    "derecho": [
        "derecho",
        "abogado",
        "abogacia",
        "abogacía",
    ],
    "diseño gráfico": [
        "diseno grafico",
        "diseño grafico",
        "diseño gráfico",
        "grafico",
        "gráfico",
    ],
    "diseño industrial": [
        "diseno industrial",
        "diseño industrial",
        "industrial diseño",
    ],
    "ingeniería civil": [
        "ingenieria civil",
        "ingeniería civil",
        "civil",
    ],
    "ingeniería electrónica": [
        "ingenieria electronica",
        "ingeniería electrónica",
        "electronica",
        "electrónica",
        "ingenieria electronica",
    ],
    "ingeniería industrial": [
        "ingenieria industrial",
        "ingeniería industrial",
        "industrial",
    ],
    "ingeniería de sistemas": [
        "sistemas",
        "ing sistemas",
        "ingenieria sistemas",
        "ingeniería sistemas",
        "ingeniería de sistemas",
        "ingenieria de sistemas",
        "guinea sistemas",
        "ingeniería de zeus",
        "ingenieria de zeus",
        "ingeniería de sus",
        "ingenieria de sus",
    ],
    "negocios internacionales": [
        "negocio internacionales",
        "negocio internacional",
        "negocios internacional",
        "negocios internacionales",
        "negocios",
        "internacionales",
    ],
}


def fix_mojibake(text: str) -> str:
    if not text:
        return ""

    text = str(text)

    for _ in range(2):
        if "Ã" in text or "Â" in text:
            try:
                fixed = text.encode("latin1").decode("utf-8")
                if fixed != text:
                    text = fixed
                    continue
            except UnicodeError:
                pass

            text = text.replace("Â", "")
            break

    return text


def normalize(text: str) -> str:
    if not text:
        return ""

    text = fix_mojibake(str(text))
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[^a-z0-9ñ\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def tokenize(text: str) -> set[str]:
    text = normalize(text)

    return {
        token
        for token in text.split()
        if len(token) >= 3 and token not in STOPWORDS
    }


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_get(row, key: str, default: str = "") -> str:
    if row is None:
        return default

    if key in row.keys():
        value = row[key]
        return fix_mojibake(str(value)) if value is not None else default

    return default


def get_all_programs():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM programs")
    rows = cur.fetchall()

    conn.close()
    return rows


def get_program_by_name(program_name: str):
    target = normalize(program_name)

    for program in get_all_programs():
        if normalize(row_get(program, "nombre")) == target:
            return program

    return None


def get_aliases_for_program(program_name: str) -> list[str]:
    normalized_name = normalize(program_name)
    aliases = []

    for key, values in MANUAL_ALIASES.items():
        if normalize(key) == normalized_name:
            aliases.extend(values)

    # Alias automáticos.
    words = normalized_name.split()

    if len(words) > 1:
        aliases.append(" ".join(words))
        aliases.append(words[-1])

    return aliases


def score_candidate(question_tokens: set[str], candidate: str) -> float:
    candidate_tokens = tokenize(candidate)

    if not candidate_tokens:
        return 0.0

    common_tokens = question_tokens.intersection(candidate_tokens)

    if not common_tokens:
        return 0.0

    return len(common_tokens) / len(candidate_tokens)


def detect_program(question: str):
    programs = get_all_programs()

    q_norm = normalize(question)
    q_tokens = tokenize(question)

    best_program = None
    best_score = 0.0

    all_program_names = []

    for program in programs:
        nombre = row_get(program, "nombre")
        titulo = row_get(program, "titulo")

        candidates = []

        if nombre:
            candidates.append(nombre)
            all_program_names.append(nombre)

        if titulo:
            candidates.append(titulo)

        candidates.extend(get_aliases_for_program(nombre))

        for candidate in candidates:
            candidate_norm = normalize(candidate)

            if not candidate_norm:
                continue

            if candidate_norm in q_norm:
                return program

            score = score_candidate(q_tokens, candidate)

            if score > best_score:
                best_score = score
                best_program = program

    # Fuzzy para frases mal transcritas.
    normalized_names = [normalize(name) for name in all_program_names]
    matches = get_close_matches(q_norm, normalized_names, n=1, cutoff=0.72)

    if matches:
        matched = matches[0]
        for program in programs:
            if normalize(row_get(program, "nombre")) == matched:
                return program

    if best_score >= 0.50:
        return best_program

    return None


def detect_intent(question: str):
    q = normalize(question)

    if any(word in q for word in ["cuanto cuesta", "valor", "precio", "costo", "matricula", "matrícula"]):
        if any(word in q for word in ["virtual"]):
            return "valor_virtual"

        if any(word in q for word in ["nocturna", "noche", "nocturno"]):
            return "valor_nocturna"

        if any(word in q for word in ["diurna", "dia", "día", "diurno"]):
            return "valor_diurna"

        return "valor"

    if any(word in q for word in ["cuanto dura", "duracion", "duración", "semestres"]):
        return "duracion"

    if any(word in q for word in ["modalidad", "presencial", "virtual"]):
        return "modalidad"

    if any(word in q for word in ["jornada", "diurna", "nocturna", "noche", "dia", "día"]):
        return "jornada"

    if any(word in q for word in ["snies", "codigo", "código"]):
        return "codigo_snies"

    if any(word in q for word in ["resolucion", "resolución", "registro calificado"]):
        return "resolucion"

    if any(word in q for word in ["creditos", "créditos"]):
        return "creditos"

    if any(word in q for word in ["contacto", "telefono", "teléfono", "correo", "email"]):
        return "contacto"

    if any(word in q for word in ["malla", "materias", "plan de estudios", "asignaturas"]):
        return "malla"

    if any(word in q for word in ["trabajo", "campo laboral", "en que puedo trabajar", "perfil ocupacional"]):
        return "trabajo"

    if any(word in q for word in ["hablame", "háblame", "hablar", "me puedes hablar", "puedes hablar", "cuentame", "cuéntame", "informacion", "información", "saber", "sobre", "la carrera"]):
        return "resumen"

    return "rag"

def money_to_int(value: str) -> int | None:
    if not value:
        return None

    digits = "".join(char for char in str(value) if char.isdigit())

    if not digits:
        return None

    return int(digits)


def extract_semesters_number(value: str) -> int | None:
    if not value:
        return None

    match = re.search(r"\d+", str(value))

    if not match:
        return None

    return int(match.group(0))


def question_asks_total_cost(question: str) -> bool:
    q = normalize(question)

    signals = [
        "total",
        "toda la carrera",
        "cuanto me cuesta toda",
        "cuanto cuesta toda",
        "valor total",
        "costo total",
        "todos los semestres",
        "los semestres",
        "completa",
    ]

    return any(signal in q for signal in signals)


def build_total_cost_text(program, question: str):
    nombre = row_get(program, "nombre")
    duracion = row_get(program, "duracion")
    semesters = extract_semesters_number(duracion)

    if not semesters:
        return ""

    q = normalize(question)

    if "virtual" in q:
        value = row_get(program, "valor_virtual")
        label = "modalidad virtual"
    elif any(word in q for word in ["nocturna", "noche", "nocturno"]):
        value = row_get(program, "valor_nocturna")
        label = "jornada nocturna"
    elif any(word in q for word in ["diurna", "dia", "diurno"]):
        value = row_get(program, "valor_diurna")
        label = "jornada diurna"
    else:
        value = (
            row_get(program, "valor_diurna")
            or row_get(program, "valor_nocturna")
            or row_get(program, "valor_virtual")
            or row_get(program, "valor_general")
        )
        label = "por semestre"

    semester_cost = money_to_int(value)

    if not semester_cost:
        return ""

    total = semester_cost * semesters

    return (
        f"{nombre} cuesta {format_money(str(semester_cost))} por semestre en {label}. "
        f"Como dura {semesters} semestres, el costo total aproximado sería de {format_money(str(total))}."
    )

def format_money(value: str):
    if not value:
        return ""

    digits = "".join(char for char in str(value) if char.isdigit())

    if not digits:
        return value

    number = int(digits)
    return f"{number:,.0f}".replace(",", ".")


def build_cost_text(program):
    """
    Aclaración por si se lo preguntan en algun momento de la historia :P
    No agregamos 'pesos' aquí porque tts_piper.py ya convierte
    1.699.200 -> un millón seiscientos... pesos colombianos.
    """
    nombre = row_get(program, "nombre")
    values = []

    diurna = row_get(program, "valor_diurna")
    nocturna = row_get(program, "valor_nocturna")
    virtual = row_get(program, "valor_virtual")
    general = row_get(program, "valor_general")

    if diurna:
        values.append(f"diurna {format_money(diurna)}")

    if nocturna:
        values.append(f"nocturna {format_money(nocturna)}")

    if virtual:
        values.append(f"virtual {format_money(virtual)}")

    if general and not values:
        values.append(format_money(general))

    if not values:
        return ""

    return f"El valor de {nombre} es: {', '.join(values)}."


def answer_exact(question: str):
    program = detect_program(question)

    if program is None:
        return None

    intent = detect_intent(question)
    nombre = row_get(program, "nombre")

    if intent in ["valor", "valor_diurna", "valor_nocturna", "valor_virtual"] and question_asks_total_cost(question):
        total_answer = build_total_cost_text(program, question)
        if total_answer:
            return total_answer

    if intent == "valor_diurna":
        value = row_get(program, "valor_diurna")
        if value:
            return f"El valor de {nombre} en jornada diurna es de {format_money(value)}."

    if intent == "valor_nocturna":
        value = row_get(program, "valor_nocturna")
        if value:
            return f"El valor de {nombre} en jornada nocturna es de {format_money(value)}."

    if intent == "valor_virtual":
        value = row_get(program, "valor_virtual")
        if value:
            return f"El valor de {nombre} en modalidad virtual es de {format_money(value)}."

    if intent == "valor":
        answer = build_cost_text(program)
        if answer:
            return answer

    if intent == "duracion":
        duracion = row_get(program, "duracion")
        if duracion:
            return f"{nombre} tiene una duración de {duracion}."

    if intent == "modalidad":
        modalidad = row_get(program, "modalidad")
        if modalidad:
            return f"La modalidad de {nombre} es {modalidad}."

    if intent == "jornada":
        jornada = row_get(program, "jornada")
        observacion = row_get(program, "observacion")

        if jornada:
            return f"La jornada de {nombre} es {jornada}."

        if observacion:
            return f"Sobre la jornada de {nombre}: {observacion}"

    if intent == "codigo_snies":
        snies = row_get(program, "codigo_snies")
        if snies:
            return f"El código SNIES de {nombre} es {snies}."

    if intent == "resolucion":
        resolucion = row_get(program, "resolucion")
        vigencia = row_get(program, "fecha_vigencia_registro_calificado")

        if resolucion and vigencia:
            return f"{nombre} tiene resolución {resolucion} y registro calificado vigente hasta {vigencia}."

        if resolucion:
            return f"La resolución de {nombre} es {resolucion}."

    if intent == "creditos":
        creditos = row_get(program, "creditos")
        if creditos:
            return f"{nombre} tiene {creditos} créditos."

    if intent == "contacto":
        telefono = row_get(program, "telefono")
        correo = row_get(program, "correo")

        parts = []
        if telefono:
            parts.append(f"teléfono {telefono}")
        if correo:
            parts.append(f"correo {correo}")

        if parts:
            return f"El contacto de {nombre} es: {', '.join(parts)}."

    if intent == "malla":
        malla = row_get(program, "malla")
        if malla:
            short = malla[:450]
            return f"La malla de {nombre} inicia así: {short}."

    if intent == "trabajo":
        trabajo = row_get(program, "trabajo")
        if trabajo:
            return f"El campo laboral de {nombre} es: {trabajo}"

    if intent == "resumen":
        titulo = row_get(program, "titulo")
        duracion = row_get(program, "duracion")
        modalidad = row_get(program, "modalidad")
        creditos = row_get(program, "creditos")

        parts = [f"{nombre} otorga el título de {titulo}" if titulo else nombre]

        if duracion:
            parts.append(f"dura {duracion}")

        if modalidad:
            parts.append(f"se ofrece en modalidad {modalidad}")

        if creditos:
            parts.append(f"tiene {creditos} créditos")

        cost = build_cost_text(program)

        answer = ", ".join(parts) + "."
        if cost:
            answer += " " + cost

        return answer

    return None


def get_program_context(program):
    if program is None:
        return ""

    fields = [
        ("Nombre", "nombre"),
        ("Título", "titulo"),
        ("Duración", "duracion"),
        ("Modalidad", "modalidad"),
        ("Jornada", "jornada"),
        ("Código SNIES", "codigo_snies"),
        ("Resolución", "resolucion"),
        ("Vigencia registro calificado", "fecha_vigencia_registro_calificado"),
        ("Créditos", "creditos"),
        ("Periodicidad de admisión", "periodicidad_admision"),
        ("Valor diurno", "valor_diurna"),
        ("Valor nocturno", "valor_nocturna"),
        ("Valor virtual", "valor_virtual"),
        ("Estado", "estado"),
        ("Observación", "observacion"),
        ("Teléfono", "telefono"),
        ("Correo", "correo"),
        ("Malla", "malla"),
        ("Campo laboral", "trabajo"),
    ]

    lines = []

    for label, key in fields:
        value = row_get(program, key)
        if value:
            lines.append(f"{label}: {value}")

    return "\n".join(lines)


def search_context(question: str, limit: int = 3):
    program = detect_program(question)

    if program is not None:
        return get_program_context(program)

    conn = get_connection()
    cur = conn.cursor()

    words = [
        normalize(word)
        for word in question.split()
        if len(normalize(word)) >= 4 and normalize(word) not in STOPWORDS
    ]

    if not words:
        conn.close()
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
            f"Título: {fix_mojibake(row['title'])}\nContenido:\n{fix_mojibake(row['content'])}"
        )

    return "\n\n---\n\n".join(context_parts)