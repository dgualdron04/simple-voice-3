"""Análisis de los registros de uso (core/usage_log.py): lo que usan el informe y el dashboard."""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

DIRECT_PREFIXES = ("knowledge", "fast", "gesture", "wake", "memory", "declined")


def group_of(route: str) -> str:
    route = route or ""
    if route.startswith("ignored"):
        return "ignorada"
    if route.startswith("llm"):
        return "llm"
    if route.startswith(("flow", "offer")):
        return "registro"
    if route.startswith(DIRECT_PREFIXES):
        return "directa"
    return "otra"


def load_events(log_dir: Path, days: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date()
    events: list[dict] = []
    for path in sorted(Path(log_dir).glob("uso_*.jsonl")):
        try:
            day = datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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


def _lat(values: list[float]) -> dict:
    return {"n": len(values), "median": round(statistics.median(values)) if values else 0,
            "p90": round(pct(values, 0.9))}


def system_warnings(log_dir: Path, limit: int = 40) -> list[str]:
    lines: list[str] = []
    for path in sorted(Path(log_dir).glob("sistema_*.log")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.search(r"\| (WARNING|ERROR|CRITICAL) ", line) and "ttfs_p99" not in line and "stop_secs" not in line:
                lines.append(line.strip()[:260])
    return lines[-limit:]


def summarize(events: list[dict], log_dir: Path | None = None, robot: str | None = None, limit: int = 40) -> dict:
    robots = sorted({e.get("robot") for e in events if e.get("robot")})
    if robot:
        events = [e for e in events if e.get("robot") == robot]
    turns = [e for e in events if e["kind"] == "turn"]
    llm = [e for e in events if e["kind"] == "llm"]
    ignored_extra = [e for e in events if e["kind"] == "ignored"]

    routes = Counter(e.get("route") or "?" for e in turns)
    groups = Counter(group_of(e.get("route")) for e in turns)
    answered = groups["directa"] + groups["llm"]

    by_hour = [0] * 24
    by_day: dict[str, dict] = {}
    for e in turns:
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, ValueError):
            continue
        by_hour[ts.hour] += 1
        d = by_day.setdefault(ts.strftime("%Y-%m-%d"), {"day": ts.strftime("%Y-%m-%d"), "atendidas": 0, "ignoradas": 0})
        d["ignoradas" if group_of(e.get("route")) == "ignorada" else "atendidas"] += 1

    stt_ms = [e["stt"]["stt_ms"] for e in turns if (e.get("stt") or {}).get("stt_ms")]
    first_tok = [e["first_token_ms"] for e in llm if e.get("first_token_ms")]
    engine_ms = [e["engine_ms"] for e in turns if e.get("engine_ms") is not None]

    ignored = [{"ts": e["ts"], "reason": (e.get("route") or e.get("reason") or "").replace("ignored:", ""),
                "heard": e.get("heard")} for e in turns if group_of(e.get("route")) == "ignorada"]
    ignored += [{"ts": e["ts"], "reason": e.get("reason"), "heard": e.get("heard")} for e in ignored_extra]
    ignored.sort(key=lambda x: x["ts"])

    questions = Counter((e.get("heard") or "", e.get("route")) for e in turns if group_of(e.get("route")) == "llm")
    doubtful = [{"ts": e["ts"], "logprob": e["stt"]["avg_logprob"], "heard": e.get("heard"), "route": e.get("route")}
                for e in turns if (e.get("stt") or {}).get("avg_logprob", 0) < -0.8]
    discarded = [{"ts": e["ts"], "logprob": e.get("avg_logprob"), "heard": e.get("discarded_text"),
                  "audio_s": e.get("audio_s")} for e in events if e["kind"] == "stt_discarded"]

    return {
        "robots": robots,
        "totals": {
            "arranques": sum(e["kind"] == "start" for e in events), "frases": len(turns),
            "atendidas": answered + groups["registro"], "ignoradas": groups["ignorada"] + len(ignored_extra),
            "directas": groups["directa"], "llm": groups["llm"], "registro": groups["registro"],
            "pct_directas": round(100 * groups["directa"] / answered) if answered else None,
            "errores": sum(e["kind"] == "error" for e in events),
            "registros_guardados": sum(e["kind"] == "registro" for e in events),
        },
        "routes": [{"route": r, "group": group_of(r), "count": n} for r, n in routes.most_common()],
        "by_hour": by_hour,
        "by_day": [by_day[k] for k in sorted(by_day)],
        "latency": {"stt": _lat(stt_ms), "llm_first_token": _lat(first_tok), "engine": _lat(engine_ms)},
        "ignored": ignored[-limit:],
        "llm_questions": [{"text": t, "route": r, "count": n} for (t, r), n in questions.most_common(limit)],
        "llm_answers": [{"ts": e["ts"], "route": e.get("route"), "ms": e.get("first_token_ms"), "answer": e.get("answer")}
                        for e in llm[-limit:]][::-1],
        "doubtful": doubtful[-limit:][::-1],
        "discarded": discarded[-limit:][::-1],
        "errors": [{"ts": e["ts"], "where": e.get("where"), "error": e.get("error")}
                   for e in events if e["kind"] == "error"][-limit:][::-1],
        "system": system_warnings(log_dir, limit) if log_dir else [],
    }
