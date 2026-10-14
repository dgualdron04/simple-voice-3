"""Servicios locales de Pipecat configurados desde el YAML del robot (STT, LLM, TTS, VAD)."""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncGenerator

import numpy as np

# Todo local: que las librerías nunca intenten bajar nada de internet.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: E402
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame  # noqa: E402
from pipecat.services.ollama.llm import OLLamaLLMService  # noqa: E402
from pipecat.services.piper.tts import PiperTTSService  # noqa: E402
from pipecat.services.whisper.stt import WhisperSTTService  # noqa: E402
from pipecat.transcriptions.language import Language  # noqa: E402
from pipecat.utils.time import time_now_iso8601  # noqa: E402

from core.config import resolve_path  # noqa: E402
from core.stt_guard import is_degenerate, looks_like_prompt_echo  # noqa: E402
from core.tts_text import clean_tts_text  # noqa: E402


class SpanishWhisperSTT(WhisperSTTService):
    """faster-whisper afinado para latencia y para no alucinar con ruido.

    beam_size=1 (greedy) es ~2-3x más rápido; sin condición con texto previo y con
    filtro interno de silencio de faster-whisper para evitar "Gracias por ver el video".
    """

    def __init__(self, *, beam_size: int = 1, **kwargs):
        super().__init__(**kwargs)
        self._beam_size = beam_size
        self.last_meta: dict = {}
        self.on_discard = None          # callable(dict) -> None, lo fija el pipeline
        # callable() -> bool. False = transcribir sin el prompt de dominio (nombres, carreras):
        # el diálogo lo apaga mientras espera un sí/no o datos personales, porque con audio corto
        # Whisper "rellena" con el prompt («Rector. Jairo Augusto Castro.» en vez de «sí»).
        self.use_domain_prompt = lambda: True

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        if not self._model:
            yield ErrorFrame("Whisper model not available")
            return

        await self.start_processing_metrics()
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        language = self._settings.language
        threshold = self._settings.no_speech_prob

        started = time.monotonic()
        domain_prompt = self._settings.initial_prompt or None
        prompt = domain_prompt if self.use_domain_prompt() else None

        def transcribe(prompt):
            segments, _ = self._model.transcribe(
                samples,
                language=language,
                beam_size=self._beam_size,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                initial_prompt=prompt,
                max_new_tokens=max(24, int(len(samples) / 16000 * 8)),     # corta bucles «la la la…»
            )
            segments = list(segments)
            kept = [s for s in segments
                    if (threshold is None or s.no_speech_prob < threshold) and s.avg_logprob > -1.3]
            return segments, kept

        all_segments, segments = await asyncio.to_thread(transcribe, prompt)
        first_text = " ".join(s.text.strip() for s in segments).strip()
        retried = False
        if prompt and first_text and (looks_like_prompt_echo(first_text, prompt) or is_degenerate(first_text)):
            # Sospecha de eco del prompt o bucle: se re-transcribe sin prompt y manda ese resultado.
            all_segments, segments = await asyncio.to_thread(transcribe, None)
            retried = True
        await self.stop_processing_metrics()
        self.last_meta = {
            "audio_s": round(len(samples) / 16000, 2),
            "stt_ms": round((time.monotonic() - started) * 1000),
            "avg_logprob": round(min((s.avg_logprob for s in segments), default=0.0), 2),
            "no_speech_prob": round(max((s.no_speech_prob for s in segments), default=0.0), 2),
            "prompt": "dominio" if prompt else "ninguno",
        }
        if retried:
            self.last_meta["retry_without_prompt"] = True      # sin el texto: no pasa por la redacción
        if all_segments and not segments and self.on_discard:
            self.on_discard({**self.last_meta, "discarded_text": " ".join(s.text.strip() for s in all_segments)})

        text = " ".join(s.text.strip() for s in segments).strip()
        if text and is_degenerate(text):
            if self.on_discard:
                self.on_discard({**self.last_meta, "discarded_text": text, "reason": "bucle"})
            text = ""
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), language)


class SpanishPiperTTS(PiperTTSService):
    """Piper con normalización de números/precios/teléfonos y pronunciación de nombres."""

    def __init__(self, *, pronunciations: dict[str, str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._pronunciations = pronunciations or {}

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        for written, spoken in self._pronunciations.items():
            text = text.replace(written, spoken)
        text = clean_tts_text(text)
        async for frame in super().run_tts(text, context_id):
            yield frame


def build_vad(settings: dict) -> SileroVADAnalyzer:
    c = settings["vad"]
    return SileroVADAnalyzer(
        sample_rate=settings["audio"]["sample_rate_in"],
        params=VADParams(confidence=c["confidence"], start_secs=c["start_secs"],
                         stop_secs=c["stop_secs"], min_volume=c["min_volume"]),
    )


def build_stt_prompt(settings: dict) -> str:
    """Vocabulario del dominio para que Whisper acierte nombres propios y carreras."""
    import re
    import sqlite3

    parts = [settings["stt"].get("initial_prompt") or ""]
    parts.append(f"Habla con {settings['robot']['name']}.")
    try:
        xml_dir = resolve_path(settings["paths"]["xml_dir"])
        for file, cargo in (("rector.xml", "Rector"), ("presidente.xml", "Presidente")):
            match = re.search(r"<nombre>([^<]+)</nombre>", (xml_dir / file).read_text(encoding="utf-8"))
            if match:
                parts.append(f"{cargo}: {match.group(1).strip()}.")
        conn = sqlite3.connect(resolve_path(settings["paths"]["database"]))
        names = sorted({r[0] for r in conn.execute("SELECT nombre FROM programs") if r[0]})
        conn.close()
        if names:
            parts.append("Programas: " + ", ".join(names) + ".")
    except Exception as error:      # sin base de conocimiento todavía: prompt básico
        print(f"[stt] prompt de dominio parcial ({error})")
    return " ".join(p for p in parts if p)


def build_stt(settings: dict) -> SpanishWhisperSTT:
    c = settings["stt"]
    return SpanishWhisperSTT(
        beam_size=c["beam_size"],
        device=c["device"],
        compute_type=c["compute_type"],
        settings=WhisperSTTService.Settings(
            model=c["model"], language=Language.ES, no_speech_prob=c["no_speech_prob"],
            initial_prompt=build_stt_prompt(settings),
        ),
    )


# Reglas fijas para cualquier robot: lo que dice el LLM se lee en voz alta y el robot solo conversa.
VOICE_RULES = (
    " Tu respuesta se lee en voz alta: texto plano, sin markdown, sin enlaces, sin emojis ni listas."
    " Solo puedes conversar: no digas que puedes subir el volumen, enviar mensajes, chatear por escrito,"
    " moverte ni hacer acciones por pedido. Si no sabes un dato de contacto o un canal, no lo inventes."
    " Si lo que oíste no tiene sentido o parece una conversación entre otras personas, pide amablemente"
    " que te repitan la pregunta."
)


def build_llm(settings: dict) -> OLLamaLLMService:
    c = settings["llm"]
    return OLLamaLLMService(
        base_url=c["base_url"],
        settings=OLLamaLLMService.Settings(
            model=c["model"], temperature=c["temperature"], max_tokens=c["max_tokens"],
            system_instruction=settings["persona"].strip() + VOICE_RULES,
        ),
    )


def build_tts(settings: dict) -> SpanishPiperTTS:
    c = settings["tts"]
    if c["engine"] != "piper":
        raise NotImplementedError(
            "tts.engine=kokoro aún no está integrado (requiere modelo; ver README). Usa piper."
        )
    voices_dir = resolve_path(c["voices_dir"])
    return SpanishPiperTTS(
        pronunciations=c.get("pronunciations"),
        download_dir=voices_dir,
        settings=PiperTTSService.Settings(voice=c["voice"]),
    )


def warm_up_llm(settings: dict) -> bool:
    """Carga el modelo en Ollama y lo deja residente (keep_alive=-1): evita 20-60 s en la 1ª respuesta."""
    import json
    import urllib.request

    c = settings["llm"]
    base = c["base_url"].rstrip("/").removesuffix("/v1")
    payload = json.dumps({"model": c["model"], "prompt": "hola", "stream": False, "keep_alive": -1,
                          "options": {"num_predict": 1}}).encode()
    request = urllib.request.Request(f"{base}/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180):
            return True
    except Exception as error:
        print(f"[warm-up] LLM no disponible ({error}). Seguiré con respuestas directas sin LLM.")
        return False
