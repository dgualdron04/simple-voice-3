from pathlib import Path
import time

import sounddevice as sd
import soundfile as sf

from TTS.api import TTS

from src.config import get_settings, resolve_path
from src.tts_text_normalizer import clean_tts_text


settings = get_settings()

XTTS_MODEL = settings["tts"].get(
    "xtts_model",
    "tts_models/multilingual/multi-dataset/xtts_v2"
)

VOICE_SAMPLE = resolve_path(settings["tts"].get("xtts_speaker_wav", "voices/my_voice/referencia.wav"))
VOICE_SAMPLE_DIR = resolve_path(settings["tts"].get("xtts_speaker_dir", "voices/xtts_samples"))

OUTPUT_WAV = resolve_path(settings["paths"].get("audio_output_xtts", "data/audio/output_xtts.wav"))
USE_GPU = bool(settings["tts"].get("xtts_gpu", False))

_tts = None
_speaker_wavs = None


def get_speaker_wavs():
    global _speaker_wavs

    if _speaker_wavs is not None:
        return _speaker_wavs

    wavs = []

    if VOICE_SAMPLE_DIR.exists():
        wavs = [str(path) for path in sorted(VOICE_SAMPLE_DIR.glob("*.wav"))]

    if not wavs and VOICE_SAMPLE.exists():
        wavs = [str(VOICE_SAMPLE)]

    if not wavs:
        raise FileNotFoundError(
            f"No encontré audios XTTS. Revisa {VOICE_SAMPLE_DIR} o {VOICE_SAMPLE}."
        )

    _speaker_wavs = wavs[:10]

    print("Audios de referencia XTTS:")
    for wav in _speaker_wavs:
        print(" -", wav)

    return _speaker_wavs


def get_tts():
    global _tts

    if _tts is None:
        print("Cargando XTTS-v2...")
        start = time.time()

        _tts = TTS(
            XTTS_MODEL,
            gpu=USE_GPU
        )

        print(f"XTTS-v2 cargado en {time.time() - start:.2f} segundos.")

    return _tts


def speak_to_file(text: str, output_path: str | Path = OUTPUT_WAV):
    tts = get_tts()
    speaker_wavs = get_speaker_wavs()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = clean_tts_text(text)

    tts.tts_to_file(
        text=text,
        speaker_wav=speaker_wavs,
        language="es",
        file_path=str(output_path)
    )

    return str(output_path)


def play_wav(path: str | Path):
    audio, sample_rate = sf.read(str(path))
    sd.play(audio, sample_rate)
    sd.wait()


def speak(text: str):
    wav_path = speak_to_file(text)
    play_wav(wav_path)


def warm_up_tts():
    start = time.time()
    speak_to_file("Hola, soy Zú. Estoy listo para ayudarte.")
    print(f"XTTS listo en {time.time() - start:.2f} segundos.")