"""Chequeo del VAD local con un wav: python tests/vad_check.py archivo.wav"""
import sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.audio_recorder import get_vad, VAD_CHUNK

w = wave.open(sys.argv[1]); assert w.getframerate() == 16000 and w.getnchannels() == 1
a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
vad = get_vad()
p = [vad.probability(a[i:i+VAD_CHUNK]) for i in range(0, len(a) - VAD_CHUNK, VAD_CHUNK)]
print(f"chunks={len(p)} max={max(p):.2f} >=0.5: {sum(x>=.5 for x in p)}")
