import json
import urllib.request
import urllib.error

from src.config import get_settings


settings = get_settings()

OLLAMA_URL = settings["llm"]["url"]
MODEL_NAME = settings["llm"]["model"]


def generate_voice_answer(
    prompt: str,
    max_tokens: int = 60,
    temperature: float = 0.45,
    print_stream: bool = False
) -> str:
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": True,
        "keep_alive": settings["llm"]["keep_alive"],
        "options": {
            "temperature": temperature,
            "top_p": 0.85,
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

    full_text = ""

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            for raw_line in response:
                if not raw_line:
                    continue

                line = raw_line.decode("utf-8").strip()

                if not line:
                    continue

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                token = chunk.get("response", "")

                if token:
                    full_text += token

                    if print_stream:
                        print(token, end="", flush=True)

                if chunk.get("done", False):
                    break

        if print_stream:
            print()

        return full_text.strip()

    except urllib.error.URLError:
        return "No pude conectarme con el modelo local. Verifica que Ollama esté abierto."

    except TimeoutError:
        return "La respuesta tardó demasiado. Intenta hacer una pregunta más específica sobre la UDI."

    except Exception as error:
        return f"Ocurrió un error generando la respuesta: {error}"


def warm_up_llm():
    return generate_voice_answer(
        "Responde solo: listo",
        max_tokens=5,
        temperature=0.1
    )