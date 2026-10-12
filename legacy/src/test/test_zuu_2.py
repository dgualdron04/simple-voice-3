# python -m src.test.test_zuu_2

from src.assistant import answer_question


questions = [
    "Háblame sobre Administración de Empresas",
    "Cuánto cuesta Administración de Empresas",
    "Cuánto cuesta Administración de Empresas de noche",

    "Háblame sobre Comunicación Social",
    "Cuánto cuesta Comunicación Social",
    "Cuál es el código SNIES de Comunicación Social",

    "Háblame sobre Criminalística",
    "Cuánto dura Criminalística",
    "Dame el contacto de Criminalística",

    "Háblame sobre Derecho",
    "Cuánto cuesta Derecho",
    "Cuántos créditos tiene Derecho",

    "Háblame sobre Diseño Gráfico",
    "Cuánto cuesta Diseño Gráfico",

    "Háblame sobre Diseño Industrial",
    "Cuál es la modalidad de Diseño Industrial",

    "Háblame sobre Ingeniería Civil",
    "Cuánto cuesta Ingeniería Civil de día",
    "Cuánto cuesta Ingeniería Civil de noche",

    "Háblame sobre Ingeniería Electrónica",
    "Cuántos créditos tiene Ingeniería Electrónica",

    "Háblame sobre Ingeniería Industrial",
    "Cuánto cuesta Ingeniería Industrial",

    "Háblame sobre Ingeniería de Sistemas",
    "Cuánto cuesta Ingeniería de Sistemas de noche",

    "Háblame sobre Negocios Internacionales",
    "Cuánto cuesta Negocios Internacionales",
    "Cuánto cuesta Negocios Internacionales virtual",

    "Háblame sobre Negocios Internacionales",
    "Cuánto cuesta",
    "Cuál es la modalidad",
    "Dame el contacto",
]

for question in questions:
    print("=" * 70)
    print("Pregunta:", question)
    print("Respuesta:", answer_question(question))