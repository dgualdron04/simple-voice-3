# Calentar modelo python -c "from src.llm import warm_up_llm; print(warm_up_llm())"

import json
import urllib.request
import urllib.error

from src.config import get_settings


settings = get_settings()

OLLAMA_URL = settings["llm"]["url"]
MODEL_NAME = settings["llm"]["model"]


def generate_voice_answer(prompt: str, max_tokens: int = 60, temperature: float = 0.45) -> str:
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "keep_alive": settings["llm"]["keep_alive"],
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_predict": max_tokens,
            "num_ctx": settings["llm"]["num_ctx"]
        }
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("response", "").strip()

    except urllib.error.URLError:
        return "No pude conectarme con el modelo local. Verifica que Ollama esté abierto."
    except Exception as error:
        return f"Ocurrió un error generando la respuesta: {error}"


def warm_up_llm():
    return generate_voice_answer(
        "Responde solo: listo",
        max_tokens=5,
        temperature=0.1
    )