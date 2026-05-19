# python -c "from src.audio_recorder import record_seconds; record_seconds(4)"

from pathlib import Path
import sounddevice as sd
from scipy.io.wavfile import write


SAMPLE_RATE = 16000
OUTPUT_PATH = Path("data/audio/input.wav")


def record_seconds(seconds: int = 4, output_path: str | Path = OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Grabando {seconds} segundos...")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16"
    )

    sd.wait()
    write(str(output_path), SAMPLE_RATE, audio)

    print(f"Audio guardado en: {output_path}")
    return str(output_path)