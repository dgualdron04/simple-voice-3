"""
Acceso al array de 4 microfonos del G1.

Hallazgo clave (no documentado en el SDK publico): el servicio "voice"
(mismo servicio que usa AudioClient para TTS/volumen) tiene un API id 1008
no registrado por defecto en el cliente, que prende/apaga el streaming del
array de microfonos:

    _RegistApi(1008, 0)
    _Call(1008, json.dumps({"mode": 1}))   # enciende el streaming
    _Call(1008, json.dumps({"mode": 2}))   # lo apaga

Una vez encendido, el audio del microfono se transmite en crudo (PCM
16-bit mono 16kHz, paquetes de 5120 bytes) por UDP multicast en
239.168.123.161:5555 -- la misma direccion documentada por otros usuarios
de G1, pero que en este robot no transmitia nada hasta activar el modo 1
via este API.

Sin este paso, el microfono simplemente no transmite nada (se confirmo
exhaustivamente: ASR del SDK, multicast sin activar, WebRTC, DDS -- todo
sin resultado hasta encontrar este API id).
"""

import json
import socket
import struct
import time
import wave

import numpy as np

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

ROBOT_API_ID_AUDIO_MIC_STREAM = 1008
MIC_MODE_ON = 1
MIC_MODE_OFF = 2

MCAST_GROUP = "239.168.123.161"
MCAST_PORT = 5555
TARGET_SAMPLE_RATE = 16000

_channel_ready = False
_audio_client = None


def _ensure_channel(network_interface: str) -> None:
    global _channel_ready

    if not _channel_ready:
        ChannelFactoryInitialize(0, network_interface)
        _channel_ready = True


def _get_audio_client(network_interface: str) -> AudioClient:
    global _audio_client

    _ensure_channel(network_interface)

    if _audio_client is None:
        _audio_client = AudioClient()
        _audio_client.SetTimeout(10.0)
        _audio_client.Init()
        _audio_client._RegistApi(ROBOT_API_ID_AUDIO_MIC_STREAM, 0)

    return _audio_client


def set_microphone_streaming(enabled: bool, network_interface: str = "eth0") -> int:
    """Prende/apaga el streaming del array de microfonos. Devuelve el
    codigo de respuesta del robot (0 = exito)."""
    client = _get_audio_client(network_interface)
    mode = MIC_MODE_ON if enabled else MIC_MODE_OFF
    code, _data = client._Call(ROBOT_API_ID_AUDIO_MIC_STREAM, json.dumps({"mode": mode}))
    return code


def record_audio(
    duration_seconds: float,
    local_ip: str = "192.168.123.164",
    network_interface: str = "eth0",
    auto_enable: bool = True,
    auto_disable: bool = True,
) -> np.ndarray:
    """
    Graba duration_seconds de audio del microfono del robot (PCM int16,
    16kHz, mono). Devuelve un numpy array de int16.

    Por defecto enciende el streaming antes de grabar y lo apaga despues
    (auto_enable/auto_disable), para no dejarlo transmitiendo de forma
    indefinida sin necesidad.
    """
    if auto_enable:
        code = set_microphone_streaming(True, network_interface=network_interface)
        if code != 0:
            raise RuntimeError(f"No se pudo encender el microfono (code={code})")
        # pequeno margen para que el stream empiece a llegar
        time.sleep(0.2)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", MCAST_PORT))

    mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GROUP), socket.inet_aton(local_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(0.5)

    frames = []
    t0 = time.time()
    try:
        while time.time() - t0 < duration_seconds:
            try:
                data, _addr = sock.recvfrom(8192)
                frames.append(data)
            except socket.timeout:
                continue
    finally:
        sock.close()
        if auto_disable:
            set_microphone_streaming(False, network_interface=network_interface)

    raw = b"".join(frames)
    return np.frombuffer(raw, dtype=np.int16)


def record_to_wav(
    output_path: str,
    duration_seconds: float,
    local_ip: str = "192.168.123.164",
    network_interface: str = "eth0",
) -> str:
    """Graba audio del microfono del robot y lo guarda como WAV 16kHz mono."""
    samples = record_audio(
        duration_seconds,
        local_ip=local_ip,
        network_interface=network_interface,
    )

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SAMPLE_RATE)
        wf.writeframes(samples.tobytes())

    return output_path
