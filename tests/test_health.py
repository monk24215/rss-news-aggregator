"""Health endpoint tests.

These verify the shape of the health response and that the root Jinja page renders.
They monkeypatch the DB/Redis checks so the tests do not require live services — CI
runs a separate real-services migration+smoke step for that.
"""

from fastapi.testclient import TestClient

import app.main as main
from app.main import app

client = TestClient(app)


def test_health_ok(monkeypatch):
    monkeypatch.setattr(main, "check_db", lambda: True)
    monkeypatch.setattr(main, "check_redis", lambda: True)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["redis"] is True


def test_health_degraded(monkeypatch):
    monkeypatch.setattr(main, "check_db", lambda: False)
    monkeypatch.setattr(main, "check_redis", lambda: True)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"


def test_index_renders(monkeypatch):
    monkeypatch.setattr(main, "check_db", lambda: True)
    monkeypatch.setattr(main, "check_redis", lambda: True)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Foundation skeleton is live" in resp.text
