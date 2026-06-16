from pathlib import Path
import shutil
import wave


SOURCE_WAV_DIR = Path(r"C:\Users\diego gualdron\Documents\projects\IA\voice\dataset\wavs")
TARGET_DIR = Path(r"C:\Users\diego gualdron\Documents\projects\udi\simple-voice-3\voices\xtts_samples")

MIN_SECONDS = 3.0
MAX_SECONDS = 12.0
MAX_SAMPLES = 10


def get_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


TARGET_DIR.mkdir(parents=True, exist_ok=True)

candidates = []

for wav_path in sorted(SOURCE_WAV_DIR.glob("*.wav")):
    try:
        duration = get_duration(wav_path)

        if MIN_SECONDS <= duration <= MAX_SECONDS:
            candidates.append((duration, wav_path))

    except Exception as error:
        print(f"No pude leer {wav_path.name}: {error}")


# Preferimos audios medianos, ni muy cortos ni muy largos.
candidates = sorted(candidates, key=lambda item: abs(item[0] - 6.0))
selected = candidates[:MAX_SAMPLES]

if not selected:
    raise RuntimeError("No encontré audios adecuados para XTTS.")

for index, (duration, wav_path) in enumerate(selected, start=1):
    target_path = TARGET_DIR / f"ref_{index:02d}.wav"
    shutil.copy2(wav_path, target_path)
    print(f"Copiado {wav_path.name} -> {target_path.name} ({duration:.2f}s)")

print()
print(f"Listo. Audios de referencia en: {TARGET_DIR}")