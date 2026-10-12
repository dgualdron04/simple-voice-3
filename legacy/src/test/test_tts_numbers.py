# python -m src.test.test_tts_numbers

from src.tts import warm_up_tts, speak


warm_up_tts()

text = (
    "El valor de Ingeniería de Sistemas en jornada nocturna es de 1.759.700. "
    "El contacto es teléfono 6352525 Ext. 107, correo je.sistemas@udi.edu.co."
)

print(text)
speak(text)