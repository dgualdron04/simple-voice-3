"""Prueba de extremo a extremo SIN hablar: inyecta transcripciones y deja que el robot responda
por el parlante. Verifica: diálogo -> (LLM) -> Piper -> salida de audio.

  python scripts/selftest_pipeline.py --robot laptop
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.frames.frames import TranscriptionFrame  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402
from pipecat.utils.time import time_now_iso8601  # noqa: E402

from core.config import load_robot  # noqa: E402
from core.pipeline import build_task  # noqa: E402
from core.services import warm_up_llm  # noqa: E402


async def main(robot, seconds):
    settings = load_robot(robot)
    task, _ = build_task(settings, warm_up_llm(settings))
    phrases = ["robot zuu cuánto cuesta derecho", "y cuánto dura", "qué opinas del café colombiano"]

    async def feed():
        await asyncio.sleep(3)
        for text in phrases:
            await task.queue_frame(TranscriptionFrame(text, "user", time_now_iso8601()))
            await asyncio.sleep(seconds / len(phrases))
        await task.stop_when_done()

    await asyncio.gather(WorkerRunner(handle_sigint=False).run(task), feed())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default=None)
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(main(args.robot, args.seconds))
