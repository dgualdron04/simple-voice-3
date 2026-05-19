import re

from src.structured_answer import get_fact_payload, search_context
from src.llm import generate_voice_answer


conversation_history = []


def clean_response(text: str) -> str:
    text = text.strip()

    prefixes = [
        "Respuesta:",
        "ZUU:",
        "Zú:",
        "Asistente:",
    ]

    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    text = text.replace("**", "")
    text = text.replace("*", "")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def add_to_history(role: str, content: str):
    conversation_history.append({
        "role": role,
        "content": content
    })

    # Guardamos solo los últimos mensajes para no hacer lento el prompt
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


def normalize_question(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("¿", "").replace("?", "")
    text = text.replace("¡", "").replace("!", "")
    text = re.sub(r"\s+", " ", text)
    return text


def is_only_greeting(question: str) -> bool:
    q = normalize_question(question)

    greetings = [
        "hola",
        "buenas",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "hey",
        "holi",
        "que tal",
        "hola zu",
        "hola zuu",
        "hola zú",
    ]

    return q in greetings


def build_fast_prompt(question: str, fact: dict) -> str:
    history = get_history_text()

    return f"""
Eres ZUU, el asistente virtual de la Universidad de Investigación y Desarrollo UDI.

Tu tarea es responder de forma natural, amable y breve.
Usa únicamente los datos exactos entregados.
No inventes información.
No digas "según la base de datos".
No uses el símbolo $.
Si mencionas un valor, escribe solo el número, por ejemplo: 1.759.700.
Responde máximo en una frase.

Historial reciente:
{history}

Pregunta del usuario:
{question}

Datos exactos:
{fact}

Respuesta:
"""


def build_rag_prompt(question: str, context: str) -> str:
    history = get_history_text()

    return f"""
Eres ZUU, el asistente virtual de la Universidad de Investigación y Desarrollo UDI.

Responde en español, de forma clara, breve y natural.
Usa únicamente el contexto.
No inventes información.
No uses el símbolo $.
Si el contexto no es suficiente, dilo con amabilidad.
Responde máximo en dos frases.

Historial reciente:
{history}

Contexto:
{context}

Pregunta del usuario:
{question}

Respuesta:
"""


def build_general_chat_prompt(question: str) -> str:
    history = get_history_text()

    return f"""
Eres ZUU, el asistente virtual de la Universidad de Investigación y Desarrollo UDI.

Puedes conversar de forma natural con el usuario.
Sé amable, breve y cercano.
No digas que eres un modelo de lenguaje.
No inventes datos académicos de la UDI si no tienes contexto.
Si el usuario pregunta por programas, costos, duración, modalidad, malla o campo laboral de la UDI, dile que puede hacerte la pregunta específica.

Historial reciente:
{history}

Usuario:
{question}

Respuesta:
"""


def answer_question(question: str):
    question = question.strip()

    if not question:
        return "No escuché una pregunta. ¿Puedes repetirla?"

    add_to_history("user", question)

    # 1. Saludo rápido sin gastar LLM
    if is_only_greeting(question):
        answer = "¡Hola! Estoy muy bien, gracias. ¿Cómo estás? También puedo ayudarte con información de la UDI."
        add_to_history("assistant", answer)
        return answer

    # 2. Preguntas exactas de la UDI: SQLite + LLM breve
    fact = get_fact_payload(question)

    if fact:
        prompt = build_fast_prompt(question, fact)

        answer = generate_voice_answer(
            prompt,
            max_tokens=45,
            temperature=0.65
        )

        answer = clean_response(answer)
        add_to_history("assistant", answer)
        return answer

    # 3. Preguntas de la UDI que necesitan contexto
    context = search_context(question, limit=1)

    if context:
        prompt = build_rag_prompt(question, context)

        answer = generate_voice_answer(
            prompt,
            max_tokens=80,
            temperature=0.5
        )

        answer = clean_response(answer)
        add_to_history("assistant", answer)
        return answer

    # 4. Conversación normal
    prompt = build_general_chat_prompt(question)

    answer = generate_voice_answer(
        prompt,
        max_tokens=70,
        temperature=0.75
    )

    answer = clean_response(answer)
    add_to_history("assistant", answer)
    return answer