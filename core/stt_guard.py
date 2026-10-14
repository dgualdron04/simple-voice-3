"""Detección de alucinaciones típicas de Whisper que llegan como texto "válido".

1. Eco del prompt: con audio corto o poco claro (un «sí» dicho a distancia), Whisper devuelve
   trozos de su `initial_prompt` («Rector. Jairo Augusto Castro.», «Presidente de la Universidad UDI.»).
2. Bucles: la misma frase repetida («Rector. Rector. Rector…», «Presidente de Colombia» ×7).
"""
from __future__ import annotations

import re
from difflib import get_close_matches

from core.registration import validators as v

# Palabras que no distinguen nada (aparecen en cualquier frase y en el prompt).
_FILLER = {"de", "del", "la", "el", "los", "las", "un", "una", "y", "o", "en", "con", "a", "al", "que",
           "es", "se", "su", "por", "para", "lo", "mi", "tu", "me", "te"}


def _content(text: str) -> list[str]:
    return [w for w in v.words(text) if w not in _FILLER and len(w) > 1]


def looks_like_prompt_echo(text: str, prompt: str | None, min_ratio: float = 0.8) -> bool:
    """True si casi todas las palabras con contenido del texto salen del prompt de Whisper.

    Solo es una sospecha: el llamador debe re-transcribir sin prompt y quedarse con ese resultado,
    porque una pregunta real («Ingeniería de Sistemas») también puede estar hecha de palabras del prompt.
    """
    if not prompt:
        return False
    words = _content(text)
    if len(words) < 2:              # «Sí.», «Derecho»: una sola palabra no basta para juzgar
        return False
    vocab = set(_content(prompt))
    inside = sum(1 for i, w in enumerate(words) if _in_vocab(w, vocab, last=i == len(words) - 1))
    return inside / len(words) >= min_ratio


def _in_vocab(word: str, vocab: set[str], last: bool) -> bool:
    """Exacta, truncada («ud» de «udi»; la última palabra puede quedar cortada: «in») o casi igual."""
    if word in vocab:
        return True
    if any(p.startswith(word) and (last or len(p) - len(word) <= 1) for p in vocab):
        return True
    return len(word) >= 5 and bool(get_close_matches(word, vocab, n=1, cutoff=0.8))


def is_degenerate(text: str, min_repeats: int = 3) -> bool:
    """True si una misma frase o palabra se repite seguida min_repeats veces o más."""
    sentences = [" ".join(v.words(s)) for s in re.split(r"[.!?¿¡,;]+", text)]
    sentences = [s for s in sentences if s]
    run = 1
    for prev, cur in zip(sentences, sentences[1:]):
        run = run + 1 if cur == prev else 1
        needed = min_repeats if " " in cur else min_repeats + 1     # «sí, sí, sí» es habla real
        if run >= needed:
            return True
    words = v.words(text)
    run = 1
    for prev, cur in zip(words, words[1:]):
        run = run + 1 if cur == prev else 1
        if run >= min_repeats + 1:          # «sí sí sí» es habla real; cuatro o más ya no
            return True
    return False
