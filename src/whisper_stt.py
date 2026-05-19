#python -c "from src.whisper_stt import warm_up_whisper; print(warm_up_whisper())"

from pathlib import Path
import time
import torch
import whisper


# Para velocidad:
# base = más rápido
# small = mejor equilibrio
# medium = más lento
WHISPER_MODEL_NAME = "medium"

_model = None


def get_device():
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def get_model():
    global _model

    if _model is None:
        device = get_device()
        print(f"Cargando Whisper '{WHISPER_MODEL_NAME}' en {device}...")

        start = time.time()
        _model = whisper.load_model(WHISPER_MODEL_NAME, device=device)

        print(f"Whisper cargado en {time.time() - start:.2f} segundos.")

    return _model


def transcribe_audio(audio_path: str | Path) -> str:
    model = get_model()
    audio_path = str(audio_path)

    device = get_device()

    result = model.transcribe(
        audio_path,
        language="es",
        fp16=(device == "cuda"),
        task="transcribe",
        verbose=False
    )

    return result["text"].strip()


def warm_up_whisper():
    """
    Solo carga el modelo para que la primera transcripción real no sea tan lenta.
    """
    get_model()
    return "Whisper listo."