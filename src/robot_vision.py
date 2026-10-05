import json
import urllib.error
import urllib.request
import base64

import cv2

from src.config import get_settings
from src.llm import generate_voice_answer

settings = get_settings()

_vision_settings = settings.get("vision", {})
OLLAMA_GENERATE_URL = _vision_settings.get("ollama_url", "http://localhost:11434/api/generate")
VISION_MODEL = _vision_settings.get("model", "moondream")

# Camara infrarroja/blanco y negro (424x240, GREY) -- no la usa videohub_pc4,
# a diferencia de la camara de color (/dev/video4).
CAMERA_DEVICE = _vision_settings.get("camera_device", "/dev/video2")
FRAME_WIDTH = 424
FRAME_HEIGHT = 240

# moondream produce resultados mucho mejores en ingles que en espanol
# (probado: en espanol devolvia texto sin sentido). Se le pide la lista en
# ingles y luego se traduce con el modelo de texto (qwen2.5), que si maneja
# bien el espanol.
VISION_PROMPT = (
    "This is a real low-resolution black-and-white infrared photo from a "
    "robot's camera, currently pointed toward the ground. Look carefully at "
    "the actual pixels in THIS specific image only. List the floor/ground "
    "itself as one item, plus any other distinct object you can ACTUALLY "
    "see with reasonable confidence (furniture, people, bags, or anything "
    "else) -- but only if it is really visible in this image, not a guess "
    "or a generic example. If you are not sure something is there, leave it "
    "out. Answer as a short comma-separated list, no explanations."
)

TRANSLATE_PROMPT_TEMPLATE = """
Traduce al español la siguiente lista de objetos, separada por comas.
Devuelve SOLO la lista traducida, separada por comas, sin explicaciones ni numeración.

Lista en inglés:
{items}

Lista en español:
"""


def _translate_items_to_spanish(items: list[str]) -> list[str]:
    prompt = TRANSLATE_PROMPT_TEMPLATE.format(items=", ".join(items))
    translated = generate_voice_answer(prompt, max_tokens=120, temperature=0.1)
    translated_items = _parse_items(translated)

    if len(translated_items) != len(items):
        return items

    return translated_items


def _capture_frame_jpeg_bytes():
    cap = cv2.VideoCapture(CAMERA_DEVICE, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"GREY"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    try:
        ok, frame = cap.read()
    finally:
        cap.release()

    if not ok:
        return None

    ok, buffer = cv2.imencode(".jpg", frame)
    if not ok:
        return None

    return buffer.tobytes()


def _ask_vision_model(image_b64: str) -> str | None:
    payload = {
        "model": VISION_MODEL,
        "prompt": VISION_PROMPT,
        "images": [image_b64],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 120,
        },
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
            return body.get("response", "").strip()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"[robot_vision] Error consultando el modelo de vision: {error}")
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _parse_items(raw_response: str) -> list[str]:
    normalized = raw_response.replace("\n", ",")
    raw_items = [item.strip(" .-[]'\"") for item in normalized.split(",")]
    raw_items = [item for item in raw_items if item]

    # El modelo de vision a veces repite la misma palabra varias veces
    # (p. ej. "piso, suelo, piso, suelo, ..."); se deduplica por texto
    # normalizado sin perder el orden de aparicion.
    return _dedupe(raw_items)


def describe_what_i_see() -> str:
    jpeg_bytes = _capture_frame_jpeg_bytes()

    if jpeg_bytes is None:
        return "No pude acceder a mi cámara en este momento."

    image_b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
    raw_response = _ask_vision_model(image_b64)

    if not raw_response:
        return (
            "Mi vista por el momento está hacia el suelo, y ahora mismo no "
            "logro procesar bien la imagen."
        )

    items = _parse_items(raw_response)

    if not items:
        return (
            "Mi vista por el momento está hacia el suelo, y no logro "
            "distinguir nada claro desde aquí."
        )

    items = _dedupe(_translate_items_to_spanish(items))

    count = len(items)
    plural = "cosas" if count != 1 else "cosa"
    items_text = ", ".join(items)

    return (
        f"Mi vista por el momento está hacia el suelo, pero puedo alcanzar "
        f"a ver {count} {plural}: {items_text}."
    )
