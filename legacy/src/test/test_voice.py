# python -m src.test.test_voice  
from src.assistant import answer_question
from src.tts import warm_up_tts, speak


warm_up_tts()

question = "¿Cuánto cuesta Ingeniería de Sistemas de noche?"
answer = answer_question(question)

print("Respuesta:", answer)
speak(answer)