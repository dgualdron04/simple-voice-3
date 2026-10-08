"""
Grabador de audio que usa el microfono propio del G1 (via robot_microphone.py)
en vez del microfono local de la Jetson (sounddevice). Misma logica de
deteccion de silencio que audio_recorder.py, para poder usarse como
reemplazo directo en el pipeline de voz.

IMPORTANTE: prender/apagar el streaming del microfono (API 1008) dispara un
aviso de audio propio del robot ("wake mode" / "wake mode off"). Por eso
la sesion se abre UNA SOLA VEZ (start_microphone_session) al arrancar el
asistente y se cierra UNA SOLA VEZ (stop_microphone_session) al salir --
calibrate_noise()/record_until_silence() reutilizan esa misma sesion en
cada llamada, en vez de prender/apagar el microfono cada vez (eso causaba
que el robot anunciara "wake mode" cada 4-6 segundos en cada ciclo del
asistente, incluso sin hablarle).
"""

import socket
import struct
import threading
import time
import wave
from pathlib import Path

import numpy as np

from src.robot_microphone import (
    MCAST_GROUP,
    MCAST_PORT,
    TARGET_SAMPLE_RATE,
    set_microphone_streaming,
)

SAMPLE_RATE = TARGET_SAMPLE_RATE
CHANNELS = 1
OUTPUT_PATH = Path("data/audio/input_robot_mic.wav")

# Silero VAD corre 100% local con onnxruntime (sin torch ni internet).
# Mismo modelo/parametros que audio_recorder.py (micrófono local), solo
# cambia de donde vienen los bloques de audio (_read_block, no sd.InputStream).
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

_session_sock = None
_session_buffer = np.array([], dtype=np.int16)
_session_network_interface = "eth0"
_session_lock = threading.Lock()


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


def start_microphone_session(network_interface: str = "eth0", local_ip: str = "192.168.123.164") -> None:
    """Prende el microfono del robot y abre el socket multicast UNA vez.
    Llamadas repetidas mientras la sesion ya esta activa no hacen nada."""
    global _session_sock, _session_buffer, _session_network_interface

    with _session_lock:
        if _session_sock is not None:
            return

        code = set_microphone_streaming(True, network_interface=network_interface)
        if code != 0:
            raise RuntimeError(f"No se pudo encender el microfono del robot (code={code})")

        # pequeno margen para que el primer paquete ya este en camino
        time.sleep(0.2)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", MCAST_PORT))

        mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GROUP), socket.inet_aton(local_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(0.2)

        _session_sock = sock
        _session_buffer = np.array([], dtype=np.int16)
        _session_network_interface = network_interface


def stop_microphone_session() -> None:
    """Apaga el microfono del robot y cierra el socket. Seguro de llamar
    aunque la sesion no este activa."""
    global _session_sock

    with _session_lock:
        if _session_sock is None:
            return

        _session_sock.close()
        _session_sock = None

    set_microphone_streaming(False, network_interface=_session_network_interface)


def is_microphone_session_active() -> bool:
    return _session_sock is not None


def _drain_stale_audio() -> None:
    """Descarta cualquier audio acumulado desde la ultima vez que se leyo
    el socket -- el microfono del robot sigue transmitiendo todo el tiempo
    que la sesion esta activa (para no repetir el aviso de "wake mode" en
    cada turno), pero si no se lee activamente mientras el asistente carga
    modelos / piensa / habla, ese audio se va acumulando en el buffer del
    sistema operativo. Sin este drenado, la primera grabacion de un turno
    nuevo empieza leyendo audio viejo (de cuando aun no se le hablaba),
    no lo que la persona dice en el momento."""
    global _session_buffer

    if _session_sock is None:
        return

    _session_buffer = np.array([], dtype=np.int16)

    _session_sock.setblocking(False)
    try:
        while True:
            try:
                _session_sock.recvfrom(8192)
            except BlockingIOError:
                break
    finally:
        _session_sock.settimeout(0.2)


def _read_block(block_size: int) -> np.ndarray:
    global _session_buffer

    if _session_sock is None:
        raise RuntimeError(
            "La sesion del microfono del robot no esta activa. "
            "Llama start_microphone_session() antes de grabar."
        )

    while len(_session_buffer) < block_size:
        try:
            data, _addr = _session_sock.recvfrom(8192)
        except socket.timeout:
            continue

        new_samples = np.frombuffer(data, dtype=np.int16)
        _session_buffer = np.concatenate([_session_buffer, new_samples])

    block = _session_buffer[:block_size]
    _session_buffer = _session_buffer[block_size:]
    return block


def calibrate_noise(
    duration: float = 1.2,
    multiplier: float = 2.8,
    min_threshold: float = 250.0,
    max_threshold: float = 1200.0,
    block_duration: float = 0.1,
):
    """Igual que audio_recorder.calibrate_noise(), pero escuchando el
    microfono del robot (requiere que start_microphone_session() ya se
    haya llamado)."""
    print("Calibrando ruido ambiente (micrófono del robot)... guarda silencio un momento.")

    _drain_stale_audio()

    block_size = int(SAMPLE_RATE * block_duration)
    energies = []

    start_time = time.time()

    while time.time() - start_time < duration:
        block = _read_block(block_size)
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
    """Estado por grabación; la sesión ONNX se comparte entre llamadas."""

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
    _drain_stale_audio()

    frames = []
    preroll = []
    speech_run = 0
    started_speaking = False
    last_voice_time = None
    max_prob = 0.0
    start_time = time.time()

    print("Habla ahora...")

    while True:
        block = _read_block(VAD_CHUNK)

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
    """Usa Silero VAD (local) si esta disponible; si no, cae a energia.
    Requiere start_microphone_session() activa -- no prende/apaga el
    microfono por si mismo."""
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
    """Grabado por energia (igual que antes) -- usado como respaldo si
    Silero VAD no esta disponible."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _drain_stale_audio()

    block_size = int(SAMPLE_RATE * block_duration)
    frames = []

    started_speaking = False
    last_voice_time = None
    start_time = time.time()
    max_energy = 0.0

    print("Habla ahora...")

    while True:
        block = _read_block(block_size)

        energy = audio_energy(block)
        max_energy = max(max_energy, energy)

        now = time.time()
        elapsed = now - start_time

        if energy >= energy_threshold:
            started_speaking = True
            last_voice_time = now
            frames.append(block.copy())
        else:
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
