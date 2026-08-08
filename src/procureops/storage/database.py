from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")


class SQLiteDatabase:
    """Small migration-aware database wrapper for the no-Docker profile."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> tuple[str, ...]:
        applied: list[str] = []
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            existing = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
                version = path.stem
                if version in existing:
                    continue
                connection.executescript(path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (version,),
                )
                applied.append(version)
        return tuple(applied)

    def optimize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA optimize")

    def explain_query_plan(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> tuple[str, ...]:
        with self.connect() as connection:
            rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}", parameters).fetchall()
        return tuple(str(row["detail"]) for row in rows)
