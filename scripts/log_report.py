"""Resume los registros de uso en un informe corto para pegárselo a Claude (o revisarlo tú).

  python scripts/log_report.py --robot laptop            # últimos 3 días
  python scripts/log_report.py --days 7 --out informe.md
Los registros ya vienen sin datos personales (ver core/usage_log.py).
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_robot, resolve_path  # noqa: E402


def load_events(log_dir: Path, days: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date()
    events = []
    for path in sorted(log_dir.glob("uso_*.jsonl")):
        try:
            day = datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))]


def build_report(events: list[dict], log_dir: Path, days: int, limit: int = 25) -> str:
    turns = [e for e in events if e["kind"] == "turn"]
    llm = [e for e in events if e["kind"] == "llm"]
    out = [f"# Informe de uso (últimos {days} días)", ""]
    out.append(f"- Arranques: {sum(e['kind'] == 'start' for e in events)} | frases oídas: {len(turns)} | "
               f"respuestas LLM: {len(llm)} | registros guardados: {sum(e['kind'] == 'registro' for e in events)}")
    errors = [e for e in events if e["kind"] == "error"]
    out.append(f"- Errores del diálogo: {len(errors)}")

    routes = Counter(e.get("route") or "?" for e in turns)
    out += ["", "## Rutas (qué decidió el robot)"] + [f"- {r}: {n}" for r, n in routes.most_common()]

    ignored = [e for e in turns if str(e.get("route", "")).startswith("ignored")]
    ignored += [e for e in events if e["kind"] == "ignored"]
    out += ["", f"## Frases ignoradas ({len(ignored)}) — ¿se perdió algo que sí era para el robot?"]
    for e in ignored[-limit:]:
        out.append(f"- [{e.get('route') or e.get('reason')}] {e.get('heard')!r}")

    discarded = [e for e in events if e["kind"] == "stt_discarded"]
    if discarded:
        out += ["", f"## Audio descartado por baja confianza ({len(discarded)})"]
        for e in discarded[-limit:]:
            out.append(f"- logprob={e.get('avg_logprob')} no_speech={e.get('no_speech_prob')} "
                       f"{e.get('audio_s')}s → {e.get('discarded_text')!r}")

    low = [e for e in turns if (e.get("stt") or {}).get("avg_logprob", 0) < -0.8]
    out += ["", f"## Transcripciones dudosas (avg_logprob < -0.8): {len(low)}"]
    for e in low[-limit:]:
        out.append(f"- {e['stt'].get('avg_logprob')}: {e.get('heard')!r} → {e.get('route')}")

    out += ["", "## Respuestas del LLM (donde puede inventar) — revisa si son correctas"]
    for e in llm[-limit:]:
        out.append(f"- [{e.get('route')}] {e.get('first_token_ms')} ms: {e.get('answer')!r}")

    llm_turns = [e for e in turns if str(e.get("route", "")).startswith("llm")]
    out += ["", "## Preguntas que fueron al LLM (candidatas a respuesta fija / FAQ)"]
    for e in llm_turns[-limit:]:
        out.append(f"- [{e['route']}] {e.get('heard')!r}")

    stt_ms = [e["stt"]["stt_ms"] for e in turns if (e.get("stt") or {}).get("stt_ms")]
    ft = [e["first_token_ms"] for e in llm if e.get("first_token_ms")]
    out += ["", "## Latencias",
            f"- STT: mediana {statistics.median(stt_ms) if stt_ms else 0:.0f} ms, p90 {pct(stt_ms, .9):.0f} ms",
            f"- LLM primer token: mediana {statistics.median(ft) if ft else 0:.0f} ms, p90 {pct(ft, .9):.0f} ms"]

    flows = Counter(e.get("route") for e in turns if str(e.get("route", "")).startswith("flow"))
    if flows:
        out += ["", "## Registro (estados)"] + [f"- {r}: {n}" for r, n in flows.most_common()]

    sys_logs = []
    for path in sorted(log_dir.glob("sistema_*.log")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.search(r"\| (WARNING|ERROR|CRITICAL) ", line) and "ttfs_p99" not in line and "stop_secs" not in line:
                sys_logs.append(line.strip()[:240])
    out += ["", f"## Avisos y errores del sistema ({len(sys_logs)})"] + [f"- {x}" for x in sys_logs[-limit:]]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default=None)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    settings = load_robot(args.robot)
    log_dir = resolve_path(settings["logging"]["dir"])
    events = load_events(log_dir, args.days)
    if not events:
        print(f"No hay registros en {log_dir}")
        return 0
    report = build_report(events, log_dir, args.days, args.limit)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Informe escrito en {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
