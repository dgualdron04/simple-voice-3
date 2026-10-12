"""Pipeline Pipecat 100% local: mic -> VAD -> Whisper -> DialogEngine -> (LLM) -> Piper -> parlante.

Orden:  entrada -> STT -> DialogProcessor -> agregador(VAD, silencio) -> LLM -> BotTextTap -> TTS -> salida
"""
from __future__ import annotations

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.workers.runner import WorkerRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_mute import AlwaysUserMuteStrategy

from adapters.base import GestureRunner, build_gesture_runner
from core.audio import resolve_device
from core.config import get_settings, resolve_path
from core.dialog import AskLLM, DialogEngine, Gesture, Say
from core.registration.store import RegistrationStore
from core.services import build_llm, build_stt, build_tts, build_vad, warm_up_llm


class TailMuteStrategy(AlwaysUserMuteStrategy):
    """Silencia al usuario mientras el robot habla y un rato después (cola del eco del parlante)."""

    def __init__(self, tail_secs: float):
        super().__init__()
        self._tail = tail_secs
        self._stopped_at = 0.0

    async def process_frame(self, frame: Frame) -> bool:
        speaking = await super().process_frame(frame)
        if isinstance(frame, BotStoppedSpeakingFrame):
            self._stopped_at = time.monotonic()
        return speaking or (time.monotonic() - self._stopped_at < self._tail)


class DialogProcessor(FrameProcessor):
    """Intercepta cada transcripción: responde directo (rápido) o pasa al LLM con contexto."""

    def __init__(self, engine: DialogEngine, gestures: GestureRunner, context: LLMContext,
                 half_duplex: bool, tail_secs: float, llm_ok: bool = True):
        super().__init__()
        self.llm_ok = llm_ok
        self.engine = engine
        self.gestures = gestures
        self.context = context
        self._half_duplex = half_duplex
        self._tail = tail_secs
        self._bot_speaking = False
        self._stopped_at = 0.0
        self.pending_swap: tuple[str, str] | None = None   # (prompt aumentado, pregunta limpia)

    def _bot_busy(self) -> bool:
        return self._bot_speaking or (time.monotonic() - self._stopped_at < self._tail)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._stopped_at = time.monotonic()

        if not isinstance(frame, TranscriptionFrame):
            await self.push_frame(frame, direction)
            return

        # Segunda barrera anti-eco: lo que se transcribe mientras el robot habla no es el usuario.
        if self._half_duplex and self._bot_busy():
            logger.debug(f"[eco] descartado mientras habla: {frame.text!r}")
            return

        logger.info(f"Usuario: {frame.text}")
        try:
            actions = self.engine.handle(frame.text)
        except Exception as error:      # un bug de diálogo no debe tumbar la conversación
            logger.exception(f"DialogEngine falló: {error}")
            actions = [Say("Perdón, no te entendí bien. ¿Puedes repetirlo?")]

        llm_action = next((a for a in actions if isinstance(a, AskLLM)), None)
        if llm_action is None and actions:
            self.context.add_message({"role": "user", "content": frame.text})   # respuesta directa
        for action in actions:
            if isinstance(action, Gesture):
                self.gestures.request(action.name)
            elif isinstance(action, Say):
                logger.info(f"Robot: {action.text}")
                self.engine.note_bot_said(action.text)
                self.context.add_message({"role": "assistant", "content": action.text})
                await self.push_frame(TTSSpeakFrame(action.text))

        if llm_action is None:
            return

        if not self.llm_ok:      # Ollama caído: responder igual, sin quedarse mudo
            text = "Ahora no puedo conversar de temas generales, pero sí puedo ayudarte con información de la UDI."
            self.engine.note_bot_said(text)
            await self.push_frame(TTSSpeakFrame(text))
            return

        self.pending_swap = (llm_action.prompt, llm_action.plain)
        frame.text = llm_action.prompt
        await self.push_frame(frame, direction)


class BotTextTap(FrameProcessor):
    """Registra lo que dijo el LLM (anti-eco) y compacta el historial al terminar cada respuesta."""

    def __init__(self, engine: DialogEngine, dialog: DialogProcessor, context: LLMContext, max_messages: int):
        super().__init__()
        self.engine = engine
        self.dialog = dialog
        self.context = context
        self.max_messages = max_messages
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            full = "".join(self._buffer).strip()
            self._buffer = []
            if full:
                logger.info(f"Robot (LLM): {full}")
                self.engine.note_bot_said(full)
            self._compact()

        await self.push_frame(frame, direction)

    def _compact(self) -> None:
        swap = self.dialog.pending_swap
        messages = list(self.context.messages)
        if swap:
            augmented, plain = swap
            for message in reversed(messages):
                if message.get("role") == "user" and message.get("content") == augmented:
                    message["content"] = plain       # el contexto RAG no se queda en el historial
                    break
            self.dialog.pending_swap = None
        if len(messages) > self.max_messages:
            self.context.set_messages(messages[-self.max_messages:])


def build_task(settings: dict | None = None, llm_ok: bool = True) -> tuple[PipelineTask, DialogEngine]:
    settings = settings or get_settings()
    audio = settings["audio"]

    store = RegistrationStore(resolve_path(settings["paths"]["registrations_db"]),
                              resolve_path(settings["paths"]["registrations_key"]))
    purged = store.purge_older_than(settings["registration"]["retention_days"])
    if purged:
        logger.info(f"Retención: {purged} registros antiguos borrados.")

    engine = DialogEngine(settings, store=store)
    gestures = build_gesture_runner(settings)
    half_duplex = not audio["barge_in"]

    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=audio["sample_rate_in"],
        audio_out_sample_rate=audio["sample_rate_out"],
        input_device_index=resolve_device(audio["input_device"], "input"),
        output_device_index=resolve_device(audio["output_device"], "output"),
    ))

    stt, llm, tts = build_stt(settings), build_llm(settings), build_tts(settings)

    context = LLMContext()
    mute = [TailMuteStrategy(audio["post_speech_mute_secs"])] if half_duplex else []
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=build_vad(settings), user_mute_strategies=mute),
    )

    dialog = DialogProcessor(engine, gestures, context, half_duplex, audio["post_speech_mute_secs"], llm_ok)
    tap = BotTextTap(engine, dialog, context, settings["llm"]["history_messages"])

    pipeline = Pipeline([
        transport.input(), stt, dialog, user_agg, llm, tap, tts, transport.output(), assistant_agg,
    ])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=audio["sample_rate_in"],
        audio_out_sample_rate=audio["sample_rate_out"],
    ))

    return task, engine


async def run(robot: str | None = None) -> None:
    from core.config import load_robot

    settings = load_robot(robot)
    name = settings["robot"]["name"]
    logger.info(f"Iniciando {name} ({settings['robot']['model']}) | STT={settings['stt']['model']} "
                f"LLM={settings['llm']['model']} | half-duplex={not settings['audio']['barge_in']}")
    llm_ok = await asyncio.to_thread(warm_up_llm, settings)
    task, _ = build_task(settings, llm_ok)
    await WorkerRunner(handle_sigint=True).run(task)
