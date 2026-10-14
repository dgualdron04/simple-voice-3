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
from core.usage_log import Stopwatch, UsageLog, redact_text


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
                 half_duplex: bool, tail_secs: float, llm_ok: bool = True,
                 usage: UsageLog | None = None, stt=None):
        super().__init__()
        self.llm_ok = llm_ok
        self.usage = usage
        self.stt = stt
        self.llm_t0: float | None = None
        self.llm_route = ""
        self._speech_t0 = 0.0
        self.engine = engine
        self.gestures = gestures
        self.context = context
        self._half_duplex = half_duplex
        self._tail = tail_secs
        self._bot_speaking = False
        self._stopped_at = 0.0
        self.pending_swap: tuple[str, str] | None = None   # (prompt aumentado, pregunta limpia)

    async def _drop_stale_llm(self) -> None:
        """Cancela la respuesta del LLM que aún se genera para la frase anterior.

        Sin esto, si la persona dice otra cosa antes de que el LLM termine, sonaban las dos
        respuestas seguidas (la vieja llegaba tarde). La interrupción también vacía el TTS y
        el audio pendiente del altavoz.
        """
        logger.debug("[llm] respuesta pendiente descartada: llegó una frase nueva")
        await self.broadcast_interruption()
        if self.pending_swap:                   # el contexto RAG de esa pregunta no se queda en el historial
            augmented, plain = self.pending_swap
            for message in reversed(list(self.context.messages)):
                if message.get("role") == "user" and message.get("content") == augmented:
                    message["content"] = plain
                    break
            self.pending_swap = None
        if self.usage:
            self.usage.event("llm_cancelled", route=self.llm_route,
                             waited_ms=round((time.monotonic() - self.llm_t0) * 1000))
        self.llm_t0 = None

    def _bot_busy(self) -> bool:
        return self._bot_speaking or (time.monotonic() - self._stopped_at < self._tail)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._speech_t0 = time.monotonic()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._stopped_at = time.monotonic()
            if self.usage:
                self.usage.event("bot_speech", ms=round((self._stopped_at - self._speech_t0) * 1000))

        if not isinstance(frame, TranscriptionFrame):
            await self.push_frame(frame, direction)
            return

        # Segunda barrera anti-eco: lo que se transcribe mientras el robot habla no es el usuario.
        if self._half_duplex and self._bot_busy():
            logger.debug(f"[eco] descartado mientras habla: {frame.text!r}")
            if self.usage:
                self.usage.event("ignored", reason="bot_speaking",
                                 heard=redact_text(frame.text) if self.usage.log_text else "<omitido>")
            return

        logger.info(f"Usuario: {frame.text}")
        clock = Stopwatch()
        try:
            actions = self.engine.handle(frame.text)
        except Exception as error:      # un bug de diálogo no debe tumbar la conversación
            logger.exception(f"DialogEngine falló: {error}")
            actions = [Say("Perdón, no te entendí bien. ¿Puedes repetirlo?")]
            self.engine.trace = {"route": "error:engine", "error": repr(error)[:200]}
            if self.usage:
                self.usage.event("error", where="DialogEngine.handle", error=repr(error)[:300])

        if self.usage:
            self.usage.names = {w for n in self.engine.memory.values() if isinstance(n, str) for w in n.split() if len(w) > 2}
            self.usage.user_turn(frame.text, self.engine.trace, dict(getattr(self.stt, "last_meta", {}) or {}),
                                 [_describe(a) for a in actions], clock.ms())
            if self.engine.saved:
                self.usage.event("registro", **self.engine.saved)     # sin datos personales
                self.engine.saved = None

        if actions and self.llm_t0 is not None:
            await self._drop_stale_llm()        # una respuesta a la frase anterior no debe sonar después

        llm_action = next((a for a in actions if isinstance(a, AskLLM)), None)
        if llm_action is None and actions:
            self.context.add_message({"role": "user", "content": frame.text})   # respuesta directa
        for action in actions:
            if isinstance(action, Gesture):
                self.gestures.request(action.name)
            elif isinstance(action, Say):
                logger.info(f"Robot: {action.text}")
                self.engine.note_bot_said(action.text)
                await self.push_frame(TTSSpeakFrame(action.text))

        if llm_action is None:
            return

        if not self.llm_ok:      # Ollama caído: responder igual, sin quedarse mudo
            text = "Ahora no puedo conversar de temas generales, pero sí puedo ayudarte con información de la UDI."
            self.engine.note_bot_said(text)
            await self.push_frame(TTSSpeakFrame(text))
            return

        self.llm_t0 = time.monotonic()
        self.llm_route = self.engine.trace.get("route", "")
        self.pending_swap = (llm_action.prompt, llm_action.plain)
        frame.text = llm_action.prompt
        await self.push_frame(frame, direction)


class BotTextTap(FrameProcessor):
    """Registra lo que dijo el LLM (anti-eco) y compacta el historial al terminar cada respuesta."""

    def __init__(self, engine: DialogEngine, dialog: DialogProcessor, context: LLMContext, max_messages: int,
                 usage: UsageLog | None = None):
        super().__init__()
        self.usage = usage
        self._first_ms: float | None = None
        self.engine = engine
        self.dialog = dialog
        self.context = context
        self.max_messages = max_messages
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            if not self._buffer and self.dialog.llm_t0 is not None:
                self._first_ms = (time.monotonic() - self.dialog.llm_t0) * 1000
            self._buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            full = "".join(self._buffer).strip()
            self._buffer = []
            if self.usage and self.dialog.llm_t0 is not None:
                self.usage.event(
                    "llm", route=self.dialog.llm_route, first_token_ms=round(self._first_ms or 0),
                    total_ms=round((time.monotonic() - self.dialog.llm_t0) * 1000), chars=len(full),
                    answer=self.usage.clean(full) if self.usage.log_text else "<omitido>")
                self.dialog.llm_t0 = None
                self._first_ms = None
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


def build_transport(audio: dict):
    """local: PyAudio (micrófono/parlante del equipo). robot: micrófono y altavoz internos del Unitree.
    unitree: lo mismo desde el PC2 del H2 (core/unitree_audio.py, robots/pc2.yaml)."""
    common = dict(audio_in_enabled=True, audio_out_enabled=True,
                  audio_in_sample_rate=audio["sample_rate_in"], audio_out_sample_rate=audio["sample_rate_out"])
    if audio["transport"] == "robot":
        from core.robot_audio import RobotAudioParams, RobotAudioTransport
        return RobotAudioTransport(RobotAudioParams(**common, **audio["robot"]))
    if audio["transport"] == "unitree":
        from core.unitree_audio import UnitreeAudioParams, UnitreeAudioTransport
        return UnitreeAudioTransport(UnitreeAudioParams(**common, **audio["unitree"]))
    if audio["transport"] != "local":
        raise ValueError(f"audio.transport desconocido: {audio['transport']!r} (usa local, robot o unitree)")
    return LocalAudioTransport(LocalAudioTransportParams(
        **common,
        input_device_index=resolve_device(audio["input_device"], "input"),
        output_device_index=resolve_device(audio["output_device"], "output"),
    ))


def build_task(settings: dict | None = None, llm_ok: bool = True) -> tuple[PipelineTask, DialogEngine]:
    settings = settings or get_settings()
    audio = settings["audio"]

    store = RegistrationStore(resolve_path(settings["paths"]["registrations_db"]),
                              resolve_path(settings["paths"]["registrations_key"]))
    purged = store.purge_older_than(settings["registration"]["retention_days"])
    if purged:
        logger.info(f"Retención: {purged} registros antiguos borrados.")

    log_cfg = settings["logging"]
    usage = UsageLog(resolve_path(log_cfg["dir"]), settings["robot"]["id"], log_cfg["enabled"],
                     log_cfg["retention_days"], log_cfg["log_text"])
    usage.event("start", name=settings["robot"]["name"], stt=settings["stt"]["model"],
                llm=settings["llm"]["model"], llm_ok=llm_ok, half_duplex=not audio["barge_in"],
                wake=settings["wake"]["mode"], factual_policy=settings["dialog"]["factual_policy"])
    engine = DialogEngine(settings, store=store)
    gestures = build_gesture_runner(settings)
    half_duplex = not audio["barge_in"]

    transport = build_transport(audio)

    stt, llm, tts = build_stt(settings), build_llm(settings), build_tts(settings)
    # Durante el registro (sí/no, nombre, teléfono, correo) Whisper va sin el prompt de dominio:
    # con respuestas cortas lo copiaba («Rector. Jairo Augusto Castro.» en lugar de «sí»).
    stt.use_domain_prompt = lambda: engine.flow is None

    context = LLMContext()
    mute = [TailMuteStrategy(audio["post_speech_mute_secs"])] if half_duplex else []
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=build_vad(settings), user_mute_strategies=mute,
                                      empty_user_turn=None),
    )

    dialog = DialogProcessor(engine, gestures, context, half_duplex, audio["post_speech_mute_secs"], llm_ok,
                             usage=usage, stt=stt)
    stt.on_discard = lambda meta: usage.event("stt_discarded", **{
        **meta, "discarded_text": redact_text(meta.get("discarded_text", "")) if usage.log_text else "<omitido>"})
    tap = BotTextTap(engine, dialog, context, settings["llm"]["history_messages"], usage=usage)

    pipeline = Pipeline([
        transport.input(), stt, dialog, user_agg, llm, tap, tts, transport.output(), assistant_agg,
    ])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=audio["sample_rate_in"],
        audio_out_sample_rate=audio["sample_rate_out"],
    ))

    return task, engine


def _describe(action) -> dict:
    if isinstance(action, Say):
        return {"say": True, "text": action.text}
    if isinstance(action, Gesture):
        return {"gesture": action.name}
    return {"llm": True, "plain": action.plain}


async def run(robot: str | None = None) -> None:
    from core.config import load_robot

    settings = load_robot(robot)
    log_cfg = settings["logging"]
    if log_cfg["enabled"]:
        log_dir = resolve_path(log_cfg["dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        # Bitácora técnica (warnings, errores, excepciones) para diagnosticar sin supervisión.
        logger.add(log_dir / "sistema_{time:YYYY-MM-DD}.log", level="INFO", rotation="20 MB",
                   retention=f"{log_cfg['retention_days']} days", encoding="utf-8")
    name = settings["robot"]["name"]
    logger.info(f"Iniciando {name} ({settings['robot']['model']}) | STT={settings['stt']['model']} "
                f"LLM={settings['llm']['model']} | half-duplex={not settings['audio']['barge_in']}")
    llm_ok = await asyncio.to_thread(warm_up_llm, settings)
    task, _ = build_task(settings, llm_ok)
    await WorkerRunner(handle_sigint=True).run(task)
