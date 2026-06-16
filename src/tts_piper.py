from pathlib import Path
import wave
import time
import re
import platform
import subprocess

import numpy as np
import sounddevice as sd

from src.config import get_settings, resolve_path
from piper import PiperVoice
from src.tts_text_normalizer import clean_tts_text as normalize_tts_text


settings = get_settings()

VOICE_MODEL = resolve_path(settings["tts"]["voice_model"])
OUTPUT_WAV = resolve_path(settings["paths"]["audio_output"])
INCLUDE_CURRENCY_WORD = settings["tts"].get("include_currency_word", True)

_voice = None

DIGITOS = {
    "0": "cero",
    "1": "uno",
    "2": "dos",
    "3": "tres",
    "4": "cuatro",
    "5": "cinco",
    "6": "seis",
    "7": "siete",
    "8": "ocho",
    "9": "nueve",
}

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

def digits_to_words(text: str) -> str:
    digits = re.sub(r"\D", "", str(text))
    return " ".join(DIGITOS[digit] for digit in digits if digit in DIGITOS)


def normalize_phone_for_speech(match):
    main_number = match.group(1)
    extension = match.group(2)

    spoken = f"teléfono {digits_to_words(main_number)}"

    if extension:
        spoken += f", extensión {digits_to_words(extension)}"

    return spoken


def normalize_extension_for_speech(match):
    extension = match.group(1)
    return f"extensión {digits_to_words(extension)}"

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
            text = "mil"
        else:
            text = f"{number_to_spanish(miles)} mil"

        if resto:
            text += f" {number_to_spanish(resto)}"

        return text

    if n < 1_000_000_000:
        millones = n // 1_000_000
        resto = n % 1_000_000

        if millones == 1:
            text = "un millón"
        else:
            text = f"{number_to_spanish(millones)} millones"

        if resto:
            text += f" {number_to_spanish(resto)}"

        return text

    return str(n)


def get_voice():
    global _voice

    if _voice is None:
        if not VOICE_MODEL.exists():
            raise FileNotFoundError(f"No encontré la voz Piper en: {VOICE_MODEL}")

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

    text = text.replace("ZUU", "Zú")
    text = text.replace("UDI", "U D I")

    text = text.replace("**", "")
    text = text.replace("*", "")
    text = text.replace("#", "")

    # Teléfonos con extensión: teléfono 6352525 Ext. 129
    text = re.sub(
        r"\b(?:tel[eé]fono|telefono|tel\.?)\s*:?\s*(\d[\d\s().-]{5,})(?:\s*(?:ext\.?|extensi[oó]n)\s*\.?:?\s*(\d+))?",
        normalize_phone_for_speech,
        text,
        flags=re.IGNORECASE
    )

    # Extensiones sueltas: Ext. 129
    text = re.sub(
        r"\b(?:ext\.?|extensi[oó]n)\s*\.?:?\s*(\d+)\b",
        normalize_extension_for_speech,
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\$?\s*(\d{1,3}(?:[.,]\d{3})+)(?:\s*(?:pesos(?:\s+colombianos)?|cop))?",
        normalize_price_for_speech,
        text,
        flags=re.IGNORECASE
    )

    text = text.replace("$", "")
    text = text.replace(":", ". ")
    text = text.replace(";", ". ")
    text = text.replace("/", " o ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def speak_to_file(text: str, output_path: str | Path = OUTPUT_WAV):
    voice = get_voice()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = normalize_tts_text(text)

    with wave.open(str(output_path), "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)

    return str(output_path)


def read_wav(path: str | Path):
    path = Path(path)

    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise ValueError("Solo se soportan WAV int16.")

    audio = np.frombuffer(frames, dtype=np.int16)

    if channels > 1:
        audio = audio.reshape(-1, channels)

    return sample_rate, audio


def play_wav_with_sounddevice(path: str | Path):
    sample_rate, audio = read_wav(path)
    sd.play(audio, sample_rate)
    sd.wait()


def play_wav_with_system(path: str | Path):
    system = platform.system().lower()
    path = str(path)

    if "linux" in system:
        subprocess.run(["aplay", path], check=True)
        return

    if "windows" in system:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME)
        return

    raise RuntimeError("No encontré reproductor WAV compatible.")


def play_wav(path: str | Path):
    try:
        play_wav_with_sounddevice(path)
    except Exception:
        play_wav_with_system(path)


def speak(text: str):
    wav_path = speak_to_file(text)
    play_wav(wav_path)


def warm_up_tts():
    start = time.time()
    speak_to_file("Hola, soy Zú. Estoy listo para ayudarte.")
    end = time.time()
    print(f"Piper listo en {end - start:.2f} segundos.")