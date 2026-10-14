"""Transporte Pipecat con el micrófono y el parlante de un robot Unitree (H2 / G1) por la red interna.

  micrófono: multicast UDP (PCM int16 mono 16 kHz) que publica la placa de control.
  parlante:  servicio DDS "voice" (AudioClient.PlayStream, PCM int16 mono 16 kHz).

Se usa con `audio.transport: unitree` en robots/<robot>.yaml (ver robots/pc2.yaml).
"""
from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

ROBOT_SAMPLE_RATE = 16000
BYTES_PER_SECOND = ROBOT_SAMPLE_RATE * 2
FRAME_BYTES = BYTES_PER_SECOND // 50          # 20 ms hacia el VAD
SEND_BYTES = int(BYTES_PER_SECOND * 0.3)      # tramo mínimo por PlayStream
LEAD_SECS = 0.3                               # cuánto se adelanta el envío al tiempo real
FLUSH_SECS = 0.08                             # sin audio nuevo en este tiempo: se envía la cola


class UnitreeAudioParams(TransportParams):
    network_interface: str = "eth0"
    mic_group: str = "239.168.123.161"
    mic_port: int = 5555
    local_ip: str = "192.168.123.162"
    app_name: str = "zuu"


_client = None
_client_lock = threading.Lock()


def get_audio_client(network_interface: str):
    """AudioClient compartido; ChannelFactoryInitialize solo puede llamarse una vez por proceso."""
    global _client
    with _client_lock:
        if _client is None:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

            ChannelFactoryInitialize(0, network_interface)
            client = AudioClient()
            client.SetTimeout(10.0)
            client.Init()
            code, data = client.GetVolume()
            if code != 0:
                raise RuntimeError(f"El servicio de audio del robot no responde (code={code}). "
                                   "¿Está encendido el robot y responde 192.168.123.161?")
            logger.info(f"Parlante del robot listo por DDS ({network_interface}), volumen={data.get('volume')}")
            # El primer stream tras conectar suena ~1 s tarde; se "calienta" con 0,2 s de silencio.
            client.PlayStream("warmup", str(int(time.time() * 1000)), bytes(BYTES_PER_SECOND // 5))
            _client = client
    return _client


class UnitreeAudioInputTransport(BaseInputTransport):
    _params: UnitreeAudioParams

    def __init__(self, params: UnitreeAudioParams):
        super().__init__(params)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if self.sample_rate != ROBOT_SAMPLE_RATE:
            raise ValueError(f"El micrófono del robot es de {ROBOT_SAMPLE_RATE} Hz; usa audio.sample_rate_in: 16000")
        if self._thread is None:
            self._open_socket()
            self._running.set()
            self._thread = threading.Thread(target=self._reader, args=(self.get_event_loop(),), daemon=True)
            self._thread.start()
            logger.info(f"Micrófono del robot: {self._params.mic_group}:{self._params.mic_port}")
        await self.set_transport_ready(frame)

    async def cleanup(self):
        await super().cleanup()
        self._running.clear()
        if self._sock:
            self._sock.close()
            self._sock = None

    def _open_socket(self):
        p = self._params
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", p.mic_port))
        mreq = struct.pack("4s4s", socket.inet_aton(p.mic_group), socket.inet_aton(p.local_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        self._sock = sock

    def _reader(self, loop: asyncio.AbstractEventLoop):
        buffer = bytearray()
        silent_since = time.monotonic()
        while self._running.is_set() and self._sock:
            try:
                buffer.extend(self._sock.recv(4096))
                silent_since = time.monotonic()
            except socket.timeout:
                if time.monotonic() - silent_since > 5:
                    logger.warning("No llega audio del micrófono del robot desde hace 5 s.")
                    silent_since = time.monotonic()
                continue
            except OSError:
                return
            while len(buffer) >= FRAME_BYTES:
                frame = InputAudioRawFrame(audio=bytes(buffer[:FRAME_BYTES]),
                                           sample_rate=ROBOT_SAMPLE_RATE, num_channels=1)
                del buffer[:FRAME_BYTES]
                asyncio.run_coroutine_threadsafe(self.push_audio_frame(frame), loop)


class UnitreeAudioOutputTransport(BaseOutputTransport):
    """Envía el audio al parlante del robot y lo frena a tiempo real.

    write_audio_frame tarda lo que dura el audio (menos LEAD_SECS), igual que un parlante local;
    así BotStarted/StoppedSpeaking y el silenciado anti-eco coinciden con lo que suena en el robot.
    """

    _params: UnitreeAudioParams

    def __init__(self, params: UnitreeAudioParams):
        super().__init__(params)
        self._executor = ThreadPoolExecutor(max_workers=1)   # PlayStream en orden
        self._client = None
        self._pending = bytearray()
        self._stream_id = ""
        self._play_start = 0.0
        self._queued_secs = 0.0
        self._flush_handle: asyncio.TimerHandle | None = None

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if self.sample_rate != ROBOT_SAMPLE_RATE:
            raise ValueError(f"El parlante del robot es de {ROBOT_SAMPLE_RATE} Hz; usa audio.sample_rate_out: 16000")
        if self._client is None:
            self._client = await self.get_event_loop().run_in_executor(
                self._executor, get_audio_client, self._params.network_interface)
        await self.set_transport_ready(frame)

    async def cleanup(self):
        try:
            await super().cleanup()
            if self._client:
                await self.get_event_loop().run_in_executor(self._executor, self._client.PlayStop,
                                                            self._params.app_name)
        finally:
            self._executor.shutdown(wait=False)

    def _send(self, stream_id: str, pcm: bytes):
        code, _ = self._client.PlayStream(self._params.app_name, stream_id, pcm)
        if code != 0:
            logger.warning(f"PlayStream devolvió code={code}")

    def _flush(self):
        self._flush_handle = None
        if self._pending:
            data, self._pending = bytes(self._pending), bytearray()
            self.get_event_loop().run_in_executor(self._executor, self._send, self._stream_id, data)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        if not self._client:
            return False

        now = time.monotonic()
        if now > self._play_start + self._queued_secs + 0.2:      # el robot ya terminó: stream nuevo
            self._stream_id = str(int(time.time() * 1000))
            self._play_start = now
            self._queued_secs = 0.0

        self._pending.extend(frame.audio)
        self._queued_secs += len(frame.audio) / BYTES_PER_SECOND

        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None
        if len(self._pending) >= SEND_BYTES:
            self._flush()
        else:
            self._flush_handle = self.get_event_loop().call_later(FLUSH_SECS, self._flush)

        delay = self._play_start + self._queued_secs - LEAD_SECS - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        return True


class UnitreeAudioTransport(BaseTransport):
    def __init__(self, params: UnitreeAudioParams):
        super().__init__()
        self._params = params
        self._input: UnitreeAudioInputTransport | None = None
        self._output: UnitreeAudioOutputTransport | None = None

    def input(self) -> FrameProcessor:
        if not self._input:
            self._input = UnitreeAudioInputTransport(self._params)
        return self._input

    def output(self) -> FrameProcessor:
        if not self._output:
            self._output = UnitreeAudioOutputTransport(self._params)
        return self._output
