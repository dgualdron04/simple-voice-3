from pathlib import Path
import time
import wave

import numpy as np
import sounddevice as sd


SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
OUTPUT_PATH = Path("data/audio/input.wav")


def save_wav(path: str | Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    audio = np.asarray(audio, dtype=np.int16)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio.tobytes())

    return str(path)


def audio_energy(block: np.ndarray) -> float:
    block = block.astype(np.float32)

    if block.size == 0:
        return 0.0

    return float(np.sqrt(np.mean(block ** 2)))

def calibrate_noise(
    duration: float = 1.2,
    multiplier: float = 2.8,
    min_threshold: float = 250.0,
    max_threshold: float = 1200.0,
    block_duration: float = 0.1,
):
    """
    Escucha el ruido ambiente por unos segundos y calcula un umbral automático.
    Durante esta calibración el usuario debe guardar silencio.
    """
    print("Calibrando ruido ambiente... guarda silencio un momento.")

    block_size = int(SAMPLE_RATE * block_duration)
    energies = []

    start_time = time.time()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=block_size
    ) as stream:
        while time.time() - start_time < duration:
            block, _ = stream.read(block_size)
            block = block.reshape(-1)

            energy = audio_energy(block)
            energies.append(energy)

    if not energies:
        print(f"No pude calibrar. Uso umbral mínimo: {min_threshold}")
        return min_threshold

    base_noise = float(np.percentile(energies, 75))
    threshold = base_noise * multiplier

    threshold = max(min_threshold, threshold)
    threshold = min(max_threshold, threshold)

    print(f"Ruido ambiente detectado: {base_noise:.2f}")
    print(f"Umbral de voz configurado en: {threshold:.2f}")

    return threshold

def record_until_silence(
    output_path: str | Path = OUTPUT_PATH,
    max_seconds: float = 7.0,
    min_seconds: float = 0.8,
    silence_seconds: float = 0.9,
    energy_threshold: float = 350.0,
    block_duration: float = 0.1,
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    block_size = int(SAMPLE_RATE * block_duration)
    frames = []

    started_speaking = False
    last_voice_time = None
    start_time = time.time()
    max_energy = 0.0

    print("Habla ahora...")

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=block_size
    ) as stream:
        while True:
            block, _ = stream.read(block_size)
            block = block.reshape(-1)

            energy = audio_energy(block)
            max_energy = max(max_energy, energy)

            now = time.time()
            elapsed = now - start_time

            if energy >= energy_threshold:
                started_speaking = True
                last_voice_time = now
                frames.append(block.copy())

            else:
                # Guarda un poco de silencio cuando ya empezó a hablar,
                # para no cortar palabras finales.
                if started_speaking:
                    frames.append(block.copy())

            if elapsed >= max_seconds:
                break

            if elapsed >= min_seconds and started_speaking and last_voice_time:
                if now - last_voice_time >= silence_seconds:
                    break

    if not started_speaking:
        print(f"No detecté voz clara. Energía máxima: {max_energy:.2f}")
        return None

    audio = np.concatenate(frames) if frames else np.array([], dtype=np.int16)

    save_wav(output_path, audio)

    print(f"Audio guardado en: {output_path}")
    print(f"Energía máxima detectada: {max_energy:.2f}")

    return str(output_path)