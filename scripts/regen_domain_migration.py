"""Regenerate the domain-schema migration (0002) from the models.

Alembic autogenerate produces the table DDL; this script renames the revision to the
project's `NNNN_name` convention, prepends the explanatory docstring, and appends the
two trigger blocks that autogenerate cannot know about (Layer 1 immutability and
`updated_at` maintenance).

Run it only when the domain models change and 0002 has not yet shipped to an
environment you cannot rebuild. Once 0002 is deployed, write a NEW migration instead —
editing history is how staging and production drift apart.

    python scripts/regen_domain_migration.py
"""

from __future__ import annotations

import glob
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
VERSIONS = REPO / "alembic" / "versions"
TARGET = VERSIONS / "0002_domain_schema.py"
PYTHON = str(REPO / ".venv" / "bin" / "python") if (REPO / ".venv").exists() else sys.executable
ALEMBIC = str(REPO / ".venv" / "bin" / "alembic") if (REPO / ".venv").exists() else "alembic"

IMMUTABLE = (
    "original_guid",
    "original_url",
    "original_canonical_url",
    "original_headline",
    "original_description",
    "original_author",
    "original_image_url",
    "original_published_at",
    "original_payload",
)

DOCSTRING = '''"""Domain schema — the three-layer article and story model (§4.2, §6).

Revision ID: 0002_domain_schema
Revises: 0001_create_heartbeats

Creates the canonical data model: sources and tags, articles and stories, versioned AI
results, feed instances, and the operational tables (jobs, logs, audit, change history,
notifications).

Two database-level guarantees are installed at the end of `upgrade()`, because the rules
they enforce are too important to leave to application discipline:

  1. `articles_original_data_is_immutable` — rejects any UPDATE that changes an
     `original_*` column. That is Non-Negotiable #1 ("original source data is never
     overwritten by AI or system-derived content") enforced by Postgres itself. An ORM
     mistake, a stray script, or a future contributor cannot violate it.

  2. `set_updated_at` — maintains `updated_at` on every domain table using
     `clock_timestamp()`, so a row cannot be modified without the timestamp moving. The
     audit trail (§36, §37) depends on that. `clock_timestamp()` rather than `now()`
     because `now()` is fixed for the whole transaction, which would leave two changes
     in one transaction indistinguishable.

Both are reversible: `downgrade()` drops the triggers and functions before the tables.
"""'''


def timestamped_tables() -> list[str]:
    code = (
        "import json;from app.models import Base;"
        "print(json.dumps(sorted(t for t,tb in Base.metadata.tables.items() "
        "if 'updated_at' in tb.c)))"
    )
    out = subprocess.run([PYTHON, "-c", code], capture_output=True, text=True, cwd=REPO)
    out.check_returncode()
    return json.loads(out.stdout)


def build_trigger_blocks(tables: list[str]) -> tuple[str, str, str]:
    checks = "\n".join(
        f"            IF NEW.{c} IS DISTINCT FROM OLD.{c} THEN\n"
        f"                RAISE EXCEPTION 'articles.{c} is original source data and "
        f"cannot be modified (Non-Negotiable #1)'\n"
        f"                    USING ERRCODE = 'restrict_violation';\n"
        f"            END IF;"
        for c in IMMUTABLE
    )
    const = (
        "\n#: Every table carrying `updated_at`; each gets the maintenance trigger below.\n"
        "TIMESTAMPED_TABLES = (\n" + "".join(f'    "{t}",\n' for t in tables) + ")\n"
    )
    up = f'''
    # ------------------------------------------------------------------
    # Layer 1 protection — Non-Negotiable #1, enforced by the database.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_original_data_mutation()
        RETURNS trigger AS $$
        BEGIN
{checks}
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER articles_original_data_is_immutable
        BEFORE UPDATE ON articles
        FOR EACH ROW EXECUTE FUNCTION reject_original_data_mutation();
        """
    )

    # ------------------------------------------------------------------
    # updated_at maintenance — a row cannot change without the clock moving.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER {{table}}_set_updated_at BEFORE UPDATE ON {{table}} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        )
'''
    down = '''    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table};")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
    op.execute("DROP TRIGGER IF EXISTS articles_original_data_is_immutable ON articles;")
    op.execute("DROP FUNCTION IF EXISTS reject_original_data_mutation();")

'''
    return const, up, down


def main() -> int:
    # Unwind the database BEFORE deleting the revision file — alembic cannot downgrade
    # past a revision whose script no longer exists.
    subprocess.run([ALEMBIC, "downgrade", "base"], cwd=REPO, check=True, capture_output=True)
    TARGET.unlink(missing_ok=True)
    subprocess.run([ALEMBIC, "upgrade", "head"], cwd=REPO, check=True, capture_output=True)
    subprocess.run(
        [ALEMBIC, "revision", "--autogenerate", "-m", "domain schema"],
        cwd=REPO,
        check=True,
        capture_output=True,
    )

    generated = [p for p in glob.glob(str(VERSIONS / "*_domain_schema.py")) if "0002" not in p]
    if not generated:
        print("autogenerate produced nothing — are the models unchanged?", file=sys.stderr)
        return 1
    src = pathlib.Path(generated[0])
    s = src.read_text()

    rev = re.search(r"revision: str = '([0-9a-f]+)'", s).group(1)
    s = s.replace(f"revision: str = '{rev}'", "revision: str = '0002_domain_schema'")
    s = re.sub(r'^""".*?"""', DOCSTRING, s, count=1, flags=re.S)

    const, up, down = build_trigger_blocks(timestamped_tables())
    s = s.replace(
        "depends_on: Union[str, Sequence[str], None] = None\n",
        "depends_on: Union[str, Sequence[str], None] = None\n" + const,
        1,
    )
    marker = "    # ### end Alembic commands ###\n\n\ndef downgrade() -> None:\n"
    if marker not in s:
        print("could not find the downgrade marker; alembic template changed?", file=sys.stderr)
        return 1
    s = s.replace(
        marker,
        "    # ### end Alembic commands ###\n" + up + "\n\ndef downgrade() -> None:\n" + down,
        1,
    )

    TARGET.write_text(s)
    src.unlink()
    print(f"wrote {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
