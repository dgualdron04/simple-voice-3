"""Mide latencia de cada componente SIN micrófono (sirve igual en portátil, G1, H2 y Go2).

  python scripts/bench_components.py --robot laptop

1) TTS: sintetiza frases con la voz del robot (Piper) y mide tiempo al primer audio.
2) STT: transcribe ese mismo audio con Whisper (voz sintética = prueba de humo, no de precisión real).
3) LLM: tiempo hasta el primer token y total, vía Ollama.
Solo usa modelos ya descargados.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from core.config import load_robot, resolve_path  # noqa: E402

PHRASES = [
    "Hola, quiero saber cuánto cuesta ingeniería de sistemas.",
    "Mi número de celular es trescientos doce, cuatro cinco seis, siete ocho nueve cero.",
    "Quiero inscribirme en derecho en jornada nocturna.",
]


def bench_tts_stt(settings):
    from piper import PiperVoice
    from faster_whisper import WhisperModel

    c = settings["tts"]
    voice = PiperVoice.load(resolve_path(c["voices_dir"]) / f"{c['voice']}.onnx")
    s = settings["stt"]
    t0 = time.time()
    model = WhisperModel(s["model"], device=s["device"], compute_type=s["compute_type"])
    print(f"[STT] carga Whisper {s['model']} ({s['device']}/{s['compute_type']}): {time.time() - t0:.1f}s")

    for phrase in PHRASES:
        t0 = time.time()
        chunks = []
        first = None
        for chunk in voice.synthesize(phrase):
            if first is None:
                first = time.time() - t0
            chunks.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
        audio = np.concatenate(chunks)
        rate = voice.config.sample_rate
        # a 16 kHz para Whisper
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(16000, rate)
        audio16 = resample_poly(audio.astype(np.float32) / 32768.0, 16000 // g, rate // g).astype(np.float32)
        print(f"[TTS] primer audio {first * 1000:.0f} ms | total {time.time() - t0:.2f}s | audio {len(audio) / rate:.1f}s")

        t0 = time.time()
        segments, _ = model.transcribe(audio16, language="es", beam_size=s["beam_size"], temperature=0.0,
                                       condition_on_previous_text=False, vad_filter=True)
        text = " ".join(x.text.strip() for x in segments)
        print(f"[STT] {time.time() - t0:.2f}s -> {text!r}")


async def bench_llm(settings):
    from openai import AsyncOpenAI

    c = settings["llm"]
    client = AsyncOpenAI(base_url=c["base_url"], api_key="ollama")
    for q in ["¿Qué es una universidad? Responde en una frase.", "Cuéntame un chiste corto."]:
        t0 = time.time()
        first = None
        out = ""
        stream = await client.chat.completions.create(
            model=c["model"], stream=True, max_tokens=c["max_tokens"], temperature=c["temperature"],
            messages=[{"role": "system", "content": settings["persona"]}, {"role": "user", "content": q}],
        )
        async for part in stream:
            if part.choices and part.choices[0].delta.content:
                if first is None:
                    first = time.time() - t0
                out += part.choices[0].delta.content
        print(f"[LLM] primer token {first:.2f}s | total {time.time() - t0:.2f}s -> {out.strip()!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default=None)
    args = parser.parse_args()
    settings = load_robot(args.robot)
    bench_tts_stt(settings)
    asyncio.run(bench_llm(settings))


if __name__ == "__main__":
    main()
