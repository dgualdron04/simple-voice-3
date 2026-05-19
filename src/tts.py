from pathlib import Path
import wave
import time
import winsound
import re

from src.config import get_settings
from piper import PiperVoice


settings = get_settings()

VOICE_MODEL = Path(settings["tts"]["voice_model"])
OUTPUT_WAV = Path(settings["paths"]["audio_output"])

#TRUE -> PESOS COLOMBIANOS
#FALSE NUMERO NORMAL
INCLUDE_CURRENCY_WORD = settings["tts"]["include_currency_word"]

_voice = None


UNIDADES = [
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete",
    "dieciocho", "diecinueve"
]

DECENAS = {
    20: "veinte",
    30: "treinta",
    40: "cuarenta",
    50: "cincuenta",
    60: "sesenta",
    70: "setenta",
    80: "ochenta",
    90: "noventa",
}

CENTENAS = {
    100: "cien",
    200: "doscientos",
    300: "trescientos",
    400: "cuatrocientos",
    500: "quinientos",
    600: "seiscientos",
    700: "setecientos",
    800: "ochocientos",
    900: "novecientos",
}


def number_to_spanish(n: int) -> str:
    if n < 20:
        return UNIDADES[n]

    if n < 30:
        if n == 20:
            return "veinte"
        return "veinti" + UNIDADES[n - 20]

    if n < 100:
        decena = (n // 10) * 10
        unidad = n % 10

        if unidad == 0:
            return DECENAS[decena]

        return f"{DECENAS[decena]} y {UNIDADES[unidad]}"

    if n < 1000:
        if n in CENTENAS:
            return CENTENAS[n]

        centena = (n // 100) * 100
        resto = n % 100

        if centena == 100:
            return f"ciento {number_to_spanish(resto)}"

        return f"{CENTENAS[centena]} {number_to_spanish(resto)}"

    if n < 1_000_000:
        miles = n // 1000
        resto = n % 1000

        if miles == 1:
            texto = "mil"
        else:
            texto = f"{number_to_spanish(miles)} mil"

        if resto:
            texto += f" {number_to_spanish(resto)}"

        return texto

    if n < 1_000_000_000:
        millones = n // 1_000_000
        resto = n % 1_000_000

        if millones == 1:
            texto = "un millón"
        else:
            texto = f"{number_to_spanish(millones)} millones"

        if resto:
            texto += f" {number_to_spanish(resto)}"

        return texto

    return str(n)


def get_voice():
    global _voice

    if _voice is None:
        if not VOICE_MODEL.exists():
            raise FileNotFoundError(f"No encontré la voz en: {VOICE_MODEL}")

        print(f"Cargando voz Piper: {VOICE_MODEL}")
        _voice = PiperVoice.load(str(VOICE_MODEL))
        print("Voz Piper cargada.")

    return _voice


def normalize_price_for_speech(match):
    raw_number = match.group(1)

    digits = re.sub(r"\D", "", raw_number)

    if not digits:
        return raw_number

    number = int(digits)
    spoken = number_to_spanish(number)

    if INCLUDE_CURRENCY_WORD:
        spoken += " pesos colombianos"

    return spoken


def clean_tts_text(text: str) -> str:
    text = text.strip()

    # Nombre del asistente e institución
    text = text.replace("ZUU", "Zú")
    text = text.replace("UDI", "U D I")

    # Quitar markdown común
    text = text.replace("**", "")
    text = text.replace("*", "")
    text = text.replace("#", "")

    # Convierte precios tipo $1.759.700 o 1.759.700 a texto hablado natural
    text = re.sub(r"\$?\s*(\d{1,3}(?:[.,]\d{3})+)", normalize_price_for_speech, text)

    # Si queda algún símbolo $, lo quitamos, no lo convertimos en "pesos"
    text = text.replace("$", "")

    # Pausas más naturales
    text = text.replace(":", ". ")
    text = text.replace(";", ". ")
    text = text.replace("/", " o ")

    # Limpieza de espacios
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def speak_to_file(text: str, output_path: str | Path = OUTPUT_WAV):
    voice = get_voice()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = clean_tts_text(text)

    with wave.open(str(output_path), "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)

    return str(output_path)


def play_wav(path: str | Path):
    winsound.PlaySound(str(path), winsound.SND_FILENAME)


def speak(text: str):
    wav_path = speak_to_file(text)
    play_wav(wav_path)


def warm_up_tts():
    start = time.time()
    speak_to_file("Hola, soy Zú. Estoy listo para ayudarte.")
    end = time.time()
    print(f"Piper listo en {end - start:.2f} segundos.")