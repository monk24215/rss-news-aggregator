"""Health endpoint tests.

These verify the shape of the health response and that the root Jinja page renders.
They stub the probes so the tests do not require live services — CI runs a separate
real-services migration + smoke step for that (`tests/smoke.py`).
"""

from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from app.probes import Probe

client = TestClient(app)


def _stub(monkeypatch, db: Probe, redis: Probe) -> None:
    monkeypatch.setattr(main, "probe_db", lambda: db)
    monkeypatch.setattr(main, "probe_redis", lambda: redis)


def test_health_ok(monkeypatch):
    _stub(monkeypatch, Probe(True), Probe(True))
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["redis"] is True
    # A healthy payload keeps the exact Section 1 shape — no extra noise.
    assert set(body) == {"status", "db", "redis"}


def test_health_degraded(monkeypatch):
    _stub(monkeypatch, Probe(False, "OperationalError: refused", "postgresql://h:5432/db"),
          Probe(True, None, "redis://h:6379/0"))
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    # A failing payload explains itself.
    assert body["detail"]["db"]["error"].startswith("OperationalError")
    assert body["detail"]["db"]["target"] == "postgresql://h:5432/db"


def test_health_reports_redis_reason(monkeypatch):
    _stub(monkeypatch, Probe(True), Probe(False, "ConnectionError: Name or service not known",
                                          "redis://redis.railway.internal:6379/0"))
    body = client.get("/health").json()
    assert body["redis"] is False
    assert "ConnectionError" in body["detail"]["redis"]["error"]


def test_index_renders(monkeypatch):
    _stub(monkeypatch, Probe(True), Probe(True))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Foundation skeleton is live" in resp.text


def test_index_shows_failure_reason(monkeypatch):
    _stub(monkeypatch, Probe(True), Probe(False, "TimeoutError", "redis://h:6379/0"))
    resp = client.get("/")
    assert "unavailable" in resp.text
    assert "TimeoutError" in resp.text


def test_boolean_wrappers_still_work(monkeypatch):
    _stub(monkeypatch, Probe(True), Probe(False, "nope"))
    assert main.check_db() is True
    assert main.check_redis() is False
