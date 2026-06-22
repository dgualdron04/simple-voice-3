import re
import random
from src.structured_answer import (
    answer_exact,
    search_context,
    detect_program,
    row_get,
)
from src.rag_reasoner import answer_reasoning_question
from src.llm import generate_voice_answer


conversation_history = []
last_program_name = None

assistant_mode = "normal"

response_cache = {}
CACHE_MAX_ITEMS = 80

def set_assistant_mode(mode: str):
    global assistant_mode

    mode = mode.lower().strip()

    if mode not in ["normal", "feria"]:
        mode = "normal"

    assistant_mode = mode


def get_assistant_mode() -> str:
    return assistant_mode


def make_cache_key(question: str) -> str:
    return f"{assistant_mode}:{normalize_question(question)}"


def get_cached_answer(cache_key: str):
    return response_cache.get(cache_key)


def finish_answer(answer: str, cache_key: str | None = None):
    answer = clean_response(answer)

    if cache_key:
        if len(response_cache) >= CACHE_MAX_ITEMS:
            oldest_key = next(iter(response_cache))
            response_cache.pop(oldest_key, None)

        response_cache[cache_key] = answer

    add_to_history("assistant", answer)

    return answer

def normalize_question(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("¿", "").replace("?", "")
    text = text.replace("¡", "").replace("!", "")
    text = text.replace("Â¿", "").replace("Â¡", "")
    text = re.sub(r"\s+", " ", text)
    return text


def clean_response(text: str) -> str:
    text = text.strip()

    prefixes = [
        "Respuesta:",
        "ZUU:",
        "Zú:",
        "ZÃº:",
        "Asistente:",
    ]

    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    text = text.replace("**", "")
    text = text.replace("*", "")
    text = text.replace("#", "")
    text = re.sub(r"\s+", " ", text)

    return text.strip()

def limit_voice_response(text: str, max_chars: int = 420) -> str:
    text = clean_response(text)

    if len(text) <= max_chars:
        return text

    sentences = re.split(r"(?<=[.!?])\s+", text)
    result = ""

    for sentence in sentences:
        candidate = f"{result} {sentence}".strip()

        if len(candidate) <= max_chars:
            result = candidate
        else:
            break

    if result:
        return result

    return text

def extract_numeric_fragments(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)*", text)

def protect_fragments(text: str):
    """
    Protege datos que el LLM no debe cambiar:
    correos, teléfonos, extensiones y valores monetarios.
    """
    fragments = []

    patterns = [
        r"\b[\w\.-]+@[\w\.-]+\.\w+\b",
        r"\b(?:tel[eé]fono|telefono|tel\.?)\s*:?\s*\d[\d\s().-]{5,}(?:\s*(?:ext\.?|extensi[oó]n)\s*\.?:?\s*\d+)?",
        r"\b\d{1,3}(?:[.,]\d{3})+\b",
        r"\b\d{4,}\b",
    ]

    protected = text

    for pattern in patterns:
        def repl(match):
            fragments.append(match.group(0))
            return f"[[DATO_{len(fragments) - 1}]]"

        protected = re.sub(pattern, repl, protected, flags=re.IGNORECASE)

    return protected, fragments


def restore_fragments(text: str, fragments: list[str]) -> str:
    restored = text

    for index, value in enumerate(fragments):
        restored = restored.replace(f"[[DATO_{index}]]", value)

    return restored

def naturalize_exact_answer(question: str, exact_answer: str) -> str:
    """
    Permite variación natural con LLM, pero protege precios, teléfonos,
    extensiones, códigos y correos para que el TTS los lea bien.
    """
    protected_answer, fragments = protect_fragments(exact_answer)

    prompt = f"""
        Eres ZUU, asistente virtual de la UDI.

        Tu tarea es reescribir la respuesta base de forma natural, breve y conversacional.

        Reglas:
        - Usa únicamente la información de la respuesta base.
        - No agregues datos nuevos.
        - No cambies nombres de programas.
        - No cambies, borres ni modifiques tokens como [[DATO_0]], [[DATO_1]], etc.
        - No agregues la palabra "pesos" después de valores numéricos.
        - Máximo dos frases.
        - Devuelve solo la respuesta final.

        Pregunta del usuario:
        {question}

        Respuesta base:
        {protected_answer}

        Respuesta natural:
    """

    generated = generate_voice_answer(
        prompt,
        max_tokens=55,
        temperature=0.30
    )

    generated = limit_voice_response(generated, max_chars=240)

    if not generated:
        return limit_voice_response(exact_answer)

    # Si el modelo no conservó los placeholders, usamos la respuesta exacta.
    for index in range(len(fragments)):
        if f"[[DATO_{index}]]" not in generated:
            return limit_voice_response(exact_answer)

    generated = restore_fragments(generated, fragments)

    base_numbers = extract_numeric_fragments(exact_answer)

    for number in base_numbers:
        if number not in generated:
            return limit_voice_response(exact_answer)

    bad_phrases = [
        "no pude conectarme",
        "ocurrió un error",
        "no encontré esa información",
        "no encontré",
    ]

    if any(phrase in generated.lower() for phrase in bad_phrases):
        return limit_voice_response(exact_answer)

    return generated

def add_to_history(role: str, content: str):
    conversation_history.append({
        "role": role,
        "content": content
    })

    if len(conversation_history) > 8:
        conversation_history.pop(0)


def get_history_text() -> str:
    if not conversation_history:
        return "Sin historial previo."

    lines = []

    for item in conversation_history:
        role = "Usuario" if item["role"] == "user" else "ZUU"
        lines.append(f"{role}: {item['content']}")

    return "\n".join(lines)


def is_only_greeting(question: str) -> bool:
    q = normalize_question(question)

    greetings = [
        "hola",
        "buenas",
        "buenos dias",
        "buenos días",
        "buenas tardes",
        "buenas noches",
        "hey",
        "holi",
        "que tal",
        "qué tal",
        "hola zu",
        "hola zuu",
        "hola zú",
    ]

    return q in greetings

def is_thanks(question: str) -> bool:
    q = normalize_question(question)

    thanks_phrases = [
        "gracias",
        "muchas gracias",
        "mil gracias",
        "te agradezco",
        "vale gracias",
        "ok gracias",
        "listo gracias",
        "perfecto gracias",
        "muy amable",
        "gracias zu",
        "gracias zuu",
        "gracias zú",
    ]

    return any(phrase in q for phrase in thanks_phrases)

def is_small_talk(question: str) -> bool:
    q = normalize_question(question)

    small_talk_phrases = [
        "como estas",
        "cómo estás",
        "como vas",
        "cómo vas",
        "que haces",
        "qué haces",
        "quien eres",
        "quién eres",
        "como te llamas",
        "cómo te llamas",
    ]

    return any(phrase in q for phrase in small_talk_phrases)


def is_probably_udi_related(question: str) -> bool:
    if detect_program(question) is not None:
        return True

    q = normalize_question(question)

    keywords = [
        "udi",
        "universidad",
        "programa",
        "carrera",
        "facultad",
        "matricula",
        "matrícula",
        "precio",
        "costo",
        "valor",
        "semestre",
        "semestres",
        "duracion",
        "duración",
        "modalidad",
        "jornada",
        "snies",
        "resolucion",
        "resolución",
        "creditos",
        "créditos",
        "contacto",
        "correo",
        "telefono",
        "teléfono",
        "malla",
        "materias",
        "asignaturas",
        "plan de estudios",
        "campo laboral",
        "trabajo",
        "ingeniería",
        "ingenieria",
        "derecho",
        "negocios",
        "administración",
        "administracion",
        "comunicación",
        "comunicacion",
        "criminalística",
        "criminalistica",
        "diseño",
        "diseno",
        "ecopetrol",
        "empresa",
        "empresarial",
        "visita",
        "visitante",
        "visita empresarial",
        "aliado",
        "aliados",
        "convenio",
        "evento",
        "zuu",
        "asistente",
        "quien eres",
        "quién eres",
        "creador",
        "creado",
        "personal udi",
        "personal de la udi",
        "ecopetrol",
        "acciones",
        "accion",
        "acción",
        "bolsa",
        "bvc",
        "nyse",
        "adr",
        "mercado",
        "inversion",
        "inversión",
        "precio accion",
        "precio acción",
        "zuu",
        "quien te creo",
        "quién te creó",
        "quien te creó",
        "quién te creo",
        "te creo",
        "te creó",
        "quien te hizo",
        "quién te hizo",
        "creador",
        "creado",
        "creada",
        "desarrollado",
        "desarrollaron",
    ]

    return any(keyword in q for keyword in keywords)


def should_use_previous_program(question: str) -> bool:
    q = normalize_question(question)

    follow_up_markers = [
        "cuanto cuesta",
        "cuánto cuesta",
        "valor",
        "precio",
        "costo",
        "cuanto dura",
        "cuánto dura",
        "duracion",
        "duración",
        "modalidad",
        "jornada",
        "malla",
        "materias",
        "creditos",
        "créditos",
        "snies",
        "resolucion",
        "resolución",
        "contacto",
        "correo",
        "telefono",
        "teléfono",

        # NUEVOS
        "nocturna",
        "nocturno",
        "noche",
        "diurna",
        "diurno",
        "dia",
        "día",
        "virtual",
        "presencial",
        "semestre",
        "semestres",
        "cuantos meses",
        "cuántos meses",
        "costo total",
        "valor total",
        "todos los semestres",
    ]

    return any(marker in q for marker in follow_up_markers)

def resolve_question_with_context(question: str) -> str:
    global last_program_name

    detected = detect_program(question)

    if detected is not None:
        last_program_name = row_get(detected, "nombre")
        return question

    if last_program_name and should_use_previous_program(question):
        return f"{question} de {last_program_name}"

    return question


def build_rag_prompt(question: str, context: str) -> str:
    history = get_history_text()

    return f"""
            Eres ZUU, el asistente virtual de la Universidad de Investigación y Desarrollo UDI.

            Puedes responder sobre:
            - Información institucional de la UDI.
            - Programas académicos cargados en la base local.
            - Empresas, eventos o visitas cargadas en la base local.
            - Información personal autorizada de ZUU o de la UDI cargada en la base local.

            REGLAS OBLIGATORIAS:
            - Responde SOLO usando el contexto local.
            - No uses conocimiento general.
            - No inventes datos.
            - No completes información faltante.
            - No respondas como Wikipedia.
            - No hagas suposiciones.
            - No uses frases como "generalmente", "normalmente", "puede incluir" o "se refiere a".
            - Si el contexto no contiene la respuesta exacta, responde únicamente:
            "No encontré esa información en mi base local."
            - Máximo dos frases.
            - Devuelve solo la respuesta final.

            Historial reciente:
            {history}

            Contexto local:
            {context}

            Pregunta:
            {question}

            Respuesta breve:
        """


def answer_question(question: str):
    global last_program_name

    question = question.strip()

    if not question:
        return "No escuché una pregunta. ¿Puedes repetirla?"

    add_to_history("user", question)

    if is_thanks(question):
        answer = random.choice([
            "¡Con gusto! Estoy aquí para ayudarte.",
            "¡De nada! Me alegra poder ayudarte.",
            "Con mucho gusto. ¿Necesitas otra información de la UDI?",
        ])
        return finish_answer(answer)

    if is_only_greeting(question):
        if assistant_mode == "feria":
            answer = "¡Hola! Soy Zú. Puedo contarte rápido sobre programas, costos, duración, modalidad y contacto de la UDI."
        else:
            answer = "¡Hola! Soy Zú, el asistente virtual de la UDI. ¿Cómo estás?"

        return finish_answer(answer)

    if is_small_talk(question):
        answer = random.choice([
            "Estoy muy bien, gracias. ¿Y tú?",
            "Todo bien por aquí, listo para ayudarte.",
            "Estoy activo y listo para ayudarte con información de la UDI.",
        ])
        return finish_answer(answer)

    resolved_question = resolve_question_with_context(question)

    cache_key = make_cache_key(resolved_question)
    cached_answer = get_cached_answer(cache_key)

    if cached_answer:
        return finish_answer(cached_answer)

    reasoned_answer = answer_reasoning_question(resolved_question)

    if reasoned_answer:
        max_chars = 260 if assistant_mode == "feria" else 520
        answer = limit_voice_response(reasoned_answer, max_chars=max_chars)
        return finish_answer(answer, cache_key)

    exact_answer = answer_exact(resolved_question)

    if exact_answer:
        max_chars = 260 if assistant_mode == "feria" else 520
        answer = limit_voice_response(exact_answer, max_chars=max_chars)
        return finish_answer(answer, cache_key)

    if not is_probably_udi_related(resolved_question):
        answer = "Solo puedo ayudarte con información relacionada con la Universidad de Investigación y Desarrollo, UDI."
        return finish_answer(answer)

    context = search_context(resolved_question, limit=2)

    if not context:
        answer = "No encontré esa información en mi base local de la UDI."
        return finish_answer(answer)

    prompt = build_rag_prompt(resolved_question, context)

    if assistant_mode == "feria":
        prompt += """
        Modo feria activado:
        - Responde como asesor de feria universitaria.
        - Sé breve, claro y amable.
        - Prioriza datos útiles para visitantes.
        - Máximo una frase corta.
        """

    answer = generate_voice_answer(
        prompt,
        max_tokens=35 if assistant_mode == "feria" else 55,
        temperature=0.15
    )

    answer = limit_voice_response(
        answer,
        max_chars=170 if assistant_mode == "feria" else 260
    )

    forbidden_signals = [
        "guinea",
        "zeus",
        "mitología",
        "pais",
        "país",
        "organización con ese nombre",
        "disciplina académica reconocida",
        "nombre específico no mencionado",
        "universidad nombre específico",
        "generalmente",
        "normalmente",
        "puede incluir",
        "rama de la ingeniería",
        "conjuntos interconectados",
        "wikipedia",
        "en términos generales",
        "se refiere a",
    ]

    lowered = answer.lower()

    if any(signal in lowered for signal in forbidden_signals):
        answer = "No encontré esa información en mi base local de la UDI."

    return finish_answer(answer, cache_key)