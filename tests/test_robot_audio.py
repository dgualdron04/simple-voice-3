"""Transporte de audio del robot: selección por config y troceo/envío del audio (sin red ni robot)."""
import asyncio

import pytest
from pipecat.frames.frames import OutputAudioRawFrame
from pipecat.transports.local.audio import LocalAudioTransport

from core.config import DEFAULTS, load_robot
from core.pipeline import build_transport
from core.robot_audio import BYTES_PER_SEC, RobotAudioParams, RobotAudioTransport, RobotSpeakerOutput


def test_h2_uses_robot_transport_and_others_stay_local():
    h2 = load_robot("h2")["audio"]
    assert h2["transport"] == "robot"
    assert h2["sample_rate_in"] == h2["sample_rate_out"] == 16000
    assert isinstance(build_transport(h2), RobotAudioTransport)
    assert DEFAULTS["audio"]["transport"] == "local"


def test_unknown_transport_is_rejected():
    audio = dict(load_robot("h2")["audio"], transport="bluetooth")
    with pytest.raises(ValueError):
        build_transport(audio)


def test_speaker_sends_fixed_chunks_and_flushes_tail():
    sent = []

    class FakeClient:
        def PlayStream(self, app, stream_id, pcm):
            sent.append((app, stream_id, len(pcm)))
            return 0, None

    async def scenario():
        out = RobotSpeakerOutput(RobotAudioParams(app_name="test", chunk_secs=0.4, lead_secs=10.0,
                                                  flush_idle_secs=0.05))
        out._client = FakeClient()
        out.get_event_loop = asyncio.get_running_loop
        flusher = asyncio.create_task(out._idle_flusher())
        frame = OutputAudioRawFrame(audio=b"\x00\x00" * 320, sample_rate=16000, num_channels=1)   # 20 ms
        for _ in range(25):                                                                        # 0,5 s
            await out.write_audio_frame(frame)
        await asyncio.sleep(0.3)
        flusher.cancel()
        out._executor.shutdown(wait=True)

    asyncio.run(scenario())
    assert [s[2] for s in sent] == [int(0.4 * BYTES_PER_SEC), int(0.1 * BYTES_PER_SEC)]
    assert {s[0] for s in sent} == {"test"}
    assert len({s[1] for s in sent}) == 1          # misma frase => mismo stream_id
