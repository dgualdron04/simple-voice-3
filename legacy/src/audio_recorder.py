from pathlib import Path
import threading
import time
import wave

import numpy as np
import sounddevice as sd


SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
OUTPUT_PATH = Path("data/audio/input.wav")

# Silero VAD corre 100% local con onnxruntime (sin torch ni internet).
# El modelo se copia una sola vez a model/silero_vad.onnx.
VAD_MODEL_PATH = Path(__file__).resolve().parent.parent / "model" / "silero_vad.onnx"
VAD_CHUNK = 512          # muestras por inferencia a 16 kHz (32 ms)
VAD_CONTEXT = 64         # muestras de contexto que exige Silero v5 a 16 kHz
VAD_START_PROB = 0.5     # probabilidad para empezar a considerar voz
VAD_KEEP_PROB = 0.35     # una vez hablando, por encima de esto sigue siendo voz
VAD_START_CHUNKS = 3     # chunks seguidos de voz para confirmar inicio (~96 ms)
VAD_PREROLL_CHUNKS = 10  # ~320 ms de audio previo para no cortar la primera sílaba

_vad_session = None
_vad_failed = False
_vad_lock = threading.Lock()


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

class SileroVAD:
    """Estado por grabación; la sesión ONNX se comparte entre hilos."""

    def __init__(self, session):
        self.session = session
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, VAD_CONTEXT), dtype=np.float32)

    def probability(self, chunk: np.ndarray) -> float:
        x = chunk.astype(np.float32).reshape(1, -1) / 32768.0
        x = np.concatenate([self.context, x], axis=1)
        self.context = x[:, -VAD_CONTEXT:]

        out, self.state = self.session.run(
            None,
            {"input": x, "state": self.state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)},
        )

        return float(out[0][0])


def get_vad():
    """Devuelve un SileroVAD o None si no está disponible (cae a energía)."""
    global _vad_session, _vad_failed

    if _vad_failed:
        return None

    with _vad_lock:
        if _vad_session is None:
            try:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                _vad_session = ort.InferenceSession(
                    str(VAD_MODEL_PATH),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
            except Exception as error:
                _vad_failed = True
                print(f"Silero VAD no disponible ({error}). Uso detección por energía.")
                return None

    return SileroVAD(_vad_session)


def record_with_vad(
    vad: SileroVAD,
    output_path: Path,
    max_seconds: float,
    min_seconds: float,
    silence_seconds: float,
):
    frames = []
    preroll = []
    speech_run = 0
    started_speaking = False
    last_voice_time = None
    max_prob = 0.0
    start_time = time.time()

    print("Habla ahora...")

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=VAD_CHUNK,
    ) as stream:
        while True:
            block, _ = stream.read(VAD_CHUNK)
            block = block.reshape(-1)

            prob = vad.probability(block)
            max_prob = max(max_prob, prob)

            now = time.time()
            elapsed = now - start_time

            if started_speaking:
                frames.append(block.copy())

                if prob >= VAD_KEEP_PROB:
                    last_voice_time = now
            else:
                preroll.append(block.copy())
                preroll = preroll[-VAD_PREROLL_CHUNKS:]
                speech_run = speech_run + 1 if prob >= VAD_START_PROB else 0

                if speech_run >= VAD_START_CHUNKS:
                    started_speaking = True
                    last_voice_time = now
                    frames.extend(preroll)

            if elapsed >= max_seconds:
                break

            if elapsed >= min_seconds and started_speaking and last_voice_time:
                if now - last_voice_time >= silence_seconds:
                    break

    if not started_speaking:
        print(f"No detecté voz. Probabilidad máxima VAD: {max_prob:.2f}")
        return None

    save_wav(output_path, np.concatenate(frames))

    print(f"Audio guardado en: {output_path}")
    print(f"Probabilidad máxima VAD: {max_prob:.2f}")

    return str(output_path)


def record_until_silence(
    output_path: str | Path = OUTPUT_PATH,
    max_seconds: float = 7.0,
    min_seconds: float = 0.8,
    silence_seconds: float = 0.9,
    energy_threshold: float = 350.0,
    block_duration: float = 0.1,
):
    """Usa Silero VAD (local). energy_threshold solo se usa si el VAD no carga."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    vad = get_vad()

    if vad is not None:
        return record_with_vad(vad, output_path, max_seconds, min_seconds, silence_seconds)

    return record_by_energy(
        output_path, max_seconds, min_seconds, silence_seconds, energy_threshold, block_duration
    )


def record_by_energy(
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