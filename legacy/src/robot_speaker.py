import time
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

TARGET_SAMPLE_RATE = 16000
CHUNK_SIZE_BYTES = 96000  # ~3s de audio a 16kHz/16-bit/mono
CHUNK_DELAY_SECONDS = 1.0

_channel_ready = False
_audio_client = None


def _get_audio_client(network_interface: str) -> AudioClient:
    global _channel_ready, _audio_client

    if not _channel_ready:
        ChannelFactoryInitialize(0, network_interface)
        _channel_ready = True

    if _audio_client is None:
        _audio_client = AudioClient()
        _audio_client.SetTimeout(10.0)
        _audio_client.Init()

    return _audio_client


def warm_up(network_interface: str = "eth0") -> None:
    """Establece la conexion DDS/AudioClient con el parlante del robot por
    adelantado, para que la primera reproduccion real no pague ese costo de
    conexion (evita que la voz arranque notoriamente mas tarde que un gesto
    que se dispare al mismo tiempo)."""
    _get_audio_client(network_interface)


def _load_wav_as_pcm16(path: str | Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise ValueError("robot_speaker solo soporta WAV de 16 bits (int16).")

    audio = np.frombuffer(frames, dtype=np.int16)

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)

    return audio, sample_rate


def _to_16k_mono_pcm_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    if sample_rate == TARGET_SAMPLE_RATE:
        return audio.astype(np.int16).tobytes()

    n_samples_out = int(round(len(audio) * TARGET_SAMPLE_RATE / sample_rate))
    resampled = resample(audio.astype(np.float32), n_samples_out)
    resampled = np.clip(resampled, -32768, 32767).astype(np.int16)

    return resampled.tobytes()


PLAYBACK_SAFETY_MARGIN_SECONDS = 0.4
BYTES_PER_SAMPLE = 2  # PCM int16


def play_wav_on_robot(
    wav_path: str | Path,
    network_interface: str = "eth0",
    app_name: str = "zuu_assistant",
) -> None:
    audio, sample_rate = _load_wav_as_pcm16(wav_path)
    pcm_data = _to_16k_mono_pcm_bytes(audio, sample_rate)
    total_duration_seconds = len(pcm_data) / (TARGET_SAMPLE_RATE * BYTES_PER_SAMPLE)

    audio_client = _get_audio_client(network_interface)

    stream_id = str(int(time.time() * 1000))
    offset = 0
    index = 0
    send_start = time.monotonic()

    while offset < len(pcm_data):
        chunk = pcm_data[offset: offset + CHUNK_SIZE_BYTES]
        code, _ = audio_client.PlayStream(app_name, stream_id, chunk)

        if code != 0:
            print(f"[robot_speaker] chunk {index} devolvio codigo de error: {code}")

        offset += CHUNK_SIZE_BYTES
        index += 1

        if offset < len(pcm_data):
            time.sleep(CHUNK_DELAY_SECONDS)

    # Esperar a que el robot termine de reproducir todo el audio ya enviado
    # (no solo un delay fijo) antes de cortar el stream con PlayStop.
    elapsed = time.monotonic() - send_start
    remaining = total_duration_seconds - elapsed + PLAYBACK_SAFETY_MARGIN_SECONDS

    if remaining > 0:
        time.sleep(remaining)

    audio_client.PlayStop(app_name)
