import http.client
import json

import pytest

from core.analytics import load_events, summarize
from core.registration.store import RegistrationStore
from dashboard.demo import build
from dashboard.server import Context, serve, serve_in_thread


@pytest.fixture(scope="module")
def demo():
    store, logs = build()
    return store, logs


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type"), data


def test_analytics_summarizes_usage(demo):
    _, logs = demo
    u = summarize(load_events(logs, 30), logs)
    t = u["totals"]
    assert t["frases"] > 100 and t["errores"] == 1
    assert 0 < t["pct_directas"] <= 100
    assert sum(u["by_hour"]) == t["frases"]
    assert u["latency"]["stt"]["median"] > 0
    assert any(r["group"] == "ignorada" for r in u["routes"])
    assert u["ignored"] and u["llm_questions"] and u["llm_answers"]
    assert summarize(load_events(logs, 30), logs, robot="h2")["totals"]["frases"] < t["frases"]


def test_server_endpoints(demo):
    store, logs = demo
    server, port = serve_in_thread(Context(store, logs, "Zuu", True, None))
    try:
        status, ctype, body = call(port, "GET", "/")
        assert status == 200 and b"Panel de robots UDI" in body
        status, _, body = call(port, "GET", "/api/meta")
        assert json.loads(body)["demo"] is True
        status, _, body = call(port, "GET", "/api/registrations")
        rows = json.loads(body)
        assert status == 200 and len(rows) == 64 and {"telefono", "estado", "programa"} <= set(rows[0])
        status, ctype, body = call(port, "GET", "/api/registrations.csv")
        assert status == 200 and ctype.startswith("text/csv") and body.startswith(b"\xef\xbb\xbf")
        status, _, body = call(port, "GET", "/api/usage?days=7")
        assert status == 200 and json.loads(body)["totals"]["frases"] > 0
        status, _, _ = call(port, "GET", "/api/usage?days=abc")
        assert status == 200                                          # valor inválido -> default, no cae
        assert call(port, "GET", "/api/nada")[0] == 404
    finally:
        server.shutdown()


def test_estado_update_and_validation(demo):
    store, logs = demo
    server, port = serve_in_thread(Context(store, logs, "Zuu", True, None))
    try:
        rid = json.loads(call(port, "GET", "/api/registrations")[2])[0]["id"]
        assert call(port, "POST", f"/api/registrations/{rid}/estado", {"estado": "contactado"})[0] == 200
        assert [r for r in store.fetch_all() if r["id"] == rid][0]["estado"] == "contactado"
        assert call(port, "POST", f"/api/registrations/{rid}/estado", {"estado": "borrado"})[0] == 400
        assert call(port, "POST", "/api/registrations/999999/estado", {"estado": "nuevo"})[0] == 404
    finally:
        server.shutdown()


def test_token_required_when_set(demo):
    store, logs = demo
    server, port = serve_in_thread(Context(store, logs, "Zuu", True, "secreto"))
    try:
        assert call(port, "GET", "/api/registrations")[0] == 401
        assert call(port, "GET", "/api/registrations", headers={"X-Token": "mal"})[0] == 401
        assert call(port, "GET", "/api/registrations", headers={"X-Token": "secreto"})[0] == 200
        assert call(port, "GET", "/api/registrations?token=secreto")[0] == 200
        assert call(port, "POST", "/api/registrations/1/estado", {"estado": "nuevo"})[0] == 401
    finally:
        server.shutdown()


def test_refuses_non_local_host_without_token(demo):
    store, logs = demo
    with pytest.raises(SystemExit):
        serve(Context(store, logs, "Zuu", True, None), host="0.0.0.0", port=0)


def test_old_database_gets_estado_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE registros (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, consent_at TEXT,"
                     " consent_version TEXT, tipo TEXT, robot_id TEXT, nombre_enc TEXT, telefono_enc TEXT, correo_enc TEXT,"
                     " programa TEXT, modalidad TEXT, jornada TEXT)")
    store = RegistrationStore(db, tmp_path / "k.key")
    rid = store.save(tipo="solo_telefono", robot_id="g1", consent_at="x", nombre=None, telefono="3001234567", correo=None)
    assert store.fetch_all()[0]["estado"] == "nuevo" and rid
