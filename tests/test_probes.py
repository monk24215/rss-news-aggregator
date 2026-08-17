"""Probe tests — the redaction rules that keep secrets out of health output.

Non-Negotiable #10 says production secrets never enter source control; the same
discipline applies to anything the app prints. A health endpoint that leaks a password
in an error message is the same class of mistake as committing one.
"""

from app.probes import Probe, describe_failure, redact_url, scrub


def test_redact_strips_credentials():
    assert redact_url("redis://default:hunter2@redis.internal:6379/0") == (
        "redis://redis.internal:6379/0"
    )
    assert redact_url("postgresql+psycopg://user:pw@db.internal:5432/app") == (
        "postgresql+psycopg://db.internal:5432/app"
    )


def test_redact_keeps_credential_free_urls_intact():
    assert redact_url("redis://localhost:6379/0") == "redis://localhost:6379/0"


def test_scrub_removes_password_from_message():
    url = "redis://default:hunter2@h:6379/0"
    assert "hunter2" not in scrub("auth failed for hunter2", url)


def test_scrub_replaces_whole_dsn():
    url = "postgresql+psycopg://u:pw@h:5432/db"
    out = scrub(f"could not connect to {url}", url)
    assert "pw" not in out
    assert "postgresql+psycopg://h:5432/db" in out


def test_scrub_is_bounded():
    assert len(scrub("x" * 5000, "redis://h:6379")) <= 300


def test_describe_failure_names_the_exception():
    out = describe_failure(TimeoutError("timed out"), "redis://d:pw@h:6379/0")
    assert out.startswith("TimeoutError")
    assert "pw" not in out


def test_probe_dict_omits_empty_fields():
    assert Probe(True).as_dict() == {"ok": True}
    assert Probe(False, "boom", "redis://h:6379").as_dict() == {
        "ok": False,
        "target": "redis://h:6379",
        "error": "boom",
    }
