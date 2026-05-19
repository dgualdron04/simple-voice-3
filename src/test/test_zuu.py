#python -m src.test.test_zuu
from src.assistant import answer_question


questions = [
    "¿Cuánto cuesta Ingeniería de Sistemas de noche?",
    "¿Cuánto cuesta Ingeniería de Sistemas de día?",
    "¿Cuánto dura Ingeniería de Sistemas?",
    "¿Cuál es la modalidad de Ingeniería de Sistemas?",
    "¿Cuál es la malla de Ingeniería de Sistemas?",
    "¿En qué puedo trabajar si estudio Ingeniería de Sistemas?",
]

for question in questions:
    print("=" * 60)
    print("Pregunta:", question)
    print("Respuesta:", answer_question(question))