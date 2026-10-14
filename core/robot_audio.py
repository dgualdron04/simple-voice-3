"""Transporte de audio Pipecat que usa el micrófono y el altavoz internos del robot Unitree (H2/G1).

* Entrada: el robot emite su micrófono por UDP multicast (por defecto 239.168.123.161:5555),
  PCM 16 bits mono a 16 kHz en paquetes de 160 ms. Se trocea en bloques de 20 ms como PyAudio.
* Salida: el audio del TTS se envía al servicio DDS `voice` con PlayStream (API 1003, igual que el
  AudioClient del G1), PCM 16 kHz mono, en bloques de ~0,4 s y con un máximo de ~1 s de adelanto
  sobre lo que el robot está reproduciendo. Al terminar una frase se vacía el resto y se cierra el stream.

Solo audio: no importa nada de locomoción (ver tests/test_safety.py).
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

ROBOT_RATE = 16000          # el micrófono y PlayStream trabajan a 16 kHz mono, 16 bits
BYTES_PER_SEC = ROBOT_RATE * 2


class RobotAudioParams(TransportParams):
    """Parámetros del transporte (además de los de TransportParams)."""

    network_interface: str = "eth10"          # interfaz DDS hacia la red 192.168.123.0/24
    interface_ip: str = "192.168.123.163"     # IP local en esa red (para unirse al multicast)
    mic_group: str = "239.168.123.161"
    mic_port: int = 5555
    app_name: str = "asistente"               # nombre de la "app" en el servicio voice del robot
    chunk_secs: float = 0.4                   # tamaño de cada envío a PlayStream
    lead_secs: float = 1.0                    # adelanto máximo sobre la reproducción del robot
    flush_idle_secs: float = 0.15             # sin audio nuevo durante este tiempo => fin de frase


class RobotMicInput(BaseInputTransport):
    _params: RobotAudioParams

    def __init__(self, params: RobotAudioParams):
        super().__init__(params)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._packets = 0

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if self.sample_rate != ROBOT_RATE:
            logger.warning(f"[robot-mic] audio_in_sample_rate={self.sample_rate}; el robot entrega {ROBOT_RATE} Hz")
        p = self._params
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", p.mic_port))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        socket.inet_aton(p.mic_group) + socket.inet_aton(p.interface_ip))
        sock.settimeout(0.5)
        self._sock, self._running = sock, True
        self._thread = threading.Thread(target=self._reader, name="robot-mic", daemon=True)
        self._thread.start()
        logger.info(f"[robot-mic] escuchando {p.mic_group}:{p.mic_port} en {p.interface_ip}")
        await self.set_transport_ready(frame)

    def _reader(self):
        loop = self.get_event_loop()
        frame_bytes = ROBOT_RATE // 50 * 2          # 20 ms
        pending = b""
        last_warn = 0.0
        while self._running:
            try:
                data = self._sock.recv(65535)
            except socket.timeout:
                if time.monotonic() - last_warn > 10:
                    logger.warning("[robot-mic] no llegan paquetes del micrófono del robot")
                    last_warn = time.monotonic()
                continue
            except OSError:
                break
            self._packets += 1
            pending += data
            while len(pending) >= frame_bytes:
                chunk, pending = pending[:frame_bytes], pending[frame_bytes:]
                frame = InputAudioRawFrame(audio=chunk, sample_rate=ROBOT_RATE, num_channels=1)
                asyncio.run_coroutine_threadsafe(self.push_audio_frame(frame), loop)

    async def cleanup(self):
        await super().cleanup()
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None


class RobotSpeakerOutput(BaseOutputTransport):
    _params: RobotAudioParams

    def __init__(self, params: RobotAudioParams):
        super().__init__(params)
        self._client = None
        self._executor = ThreadPoolExecutor(max_workers=1)   # envíos en orden
        self._buffer = bytearray()
        self._stream_id: str | None = None
        self._play_end = 0.0          # instante (monotonic) en que el robot terminará lo ya enviado
        self._last_write = 0.0
        self._flusher: asyncio.Task | None = None

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if self.sample_rate != ROBOT_RATE:
            logger.warning(f"[robot-speaker] audio_out_sample_rate={self.sample_rate}; PlayStream espera {ROBOT_RATE} Hz")
        self._client = await self.get_event_loop().run_in_executor(self._executor, self._make_client)
        self._flusher = asyncio.create_task(self._idle_flusher())
        await self.set_transport_ready(frame)

    async def process_frame(self, frame, direction: FrameDirection):
        if isinstance(frame, InterruptionFrame):
            # Lo enviado por adelantado al robot (hasta lead_secs) seguiría sonando: se corta.
            self._buffer = bytearray()
            self._stream_id = None
            self._play_end = time.monotonic()
            if self._client:
                await self.get_event_loop().run_in_executor(self._executor, self._stop)
        await super().process_frame(frame, direction)

    def _make_client(self):
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

        try:
            ChannelFactoryInitialize(0, self._params.network_interface)
        except Exception as error:            # ya inicializado por otro componente
            logger.debug(f"[robot-speaker] ChannelFactory: {error}")
        client = AudioClient()
        client.SetTimeout(5.0)
        client.Init()
        logger.info(f"[robot-speaker] altavoz del robot por DDS ({self._params.network_interface})")
        return client

    def _send(self, stream_id: str, pcm: bytes):
        code, _ = self._client.PlayStream(self._params.app_name, stream_id, pcm)
        if code != 0:
            logger.warning(f"[robot-speaker] PlayStream devolvió {code}")

    def _stop(self):
        self._client.PlayStop(self._params.app_name)

    async def _submit(self, pcm: bytes):
        now = time.monotonic()
        if self._stream_id is None:
            self._stream_id = str(int(time.time() * 1000))
        self._play_end = max(self._play_end, now) + len(pcm) / BYTES_PER_SEC
        await self.get_event_loop().run_in_executor(self._executor, self._send, self._stream_id, pcm)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        if not self._client:
            return False
        self._buffer += frame.audio
        self._last_write = time.monotonic()
        chunk_bytes = int(self._params.chunk_secs * BYTES_PER_SEC) // 2 * 2
        while len(self._buffer) >= chunk_bytes:
            pcm, self._buffer = bytes(self._buffer[:chunk_bytes]), self._buffer[chunk_bytes:]
            await self._submit(pcm)
        # Ritmo de tiempo real: no adelantarse más de lead_secs a lo que el robot reproduce.
        ahead = self._play_end - time.monotonic() - self._params.lead_secs
        if ahead > 0:
            await asyncio.sleep(ahead)
        return True

    async def _idle_flusher(self):
        """Si el TTS deja de mandar audio, envía lo que queda; cuando el robot termina, nuevo stream."""
        while True:
            await asyncio.sleep(0.05)
            if self._stream_id is None or time.monotonic() - self._last_write < self._params.flush_idle_secs:
                continue
            if self._buffer:
                pcm, self._buffer = bytes(self._buffer), bytearray()
                await self._submit(pcm)
            if time.monotonic() >= self._play_end:
                self._stream_id = None

    async def cleanup(self):
        try:
            await super().cleanup()
            if self._flusher:
                self._flusher.cancel()
            if self._client:
                await self.get_event_loop().run_in_executor(self._executor, self._stop)
        finally:
            self._executor.shutdown(wait=False)


class RobotAudioTransport(BaseTransport):
    """Micrófono (multicast) y altavoz (DDS voice) internos del robot."""

    def __init__(self, params: RobotAudioParams):
        super().__init__()
        self._params = params
        self._input: RobotMicInput | None = None
        self._output: RobotSpeakerOutput | None = None

    def input(self) -> FrameProcessor:
        if not self._input:
            self._input = RobotMicInput(self._params)
        return self._input

    def output(self) -> FrameProcessor:
        if not self._output:
            self._output = RobotSpeakerOutput(self._params)
        return self._output
