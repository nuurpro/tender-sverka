import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

JSON_FIELDS = {"files", "blocks", "requirements", "bids", "results"}
MUTABLE_FIELDS = JSON_FIELDS | {"status", "stage", "approved", "error"}
BUSY = {"queued", "running"}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = self.root / "checks.sqlite3"

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'idle',
                    stage TEXT NOT NULL DEFAULT 'extract',
                    files TEXT NOT NULL DEFAULT '[]', blocks TEXT NOT NULL DEFAULT '[]',
                    requirements TEXT NOT NULL DEFAULT '[]', bids TEXT NOT NULL DEFAULT '{}',
                    results TEXT NOT NULL DEFAULT '{}', approved INTEGER NOT NULL DEFAULT 0,
                    error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute(
                "UPDATE checks SET status='failed', error=? WHERE status IN ('queued','running')",
                ("Сервер был перезапущен. Повторите обработку.",),
            )

    def create(self, files: list[dict]) -> str:
        check_id = uuid4().hex
        with self.connection() as connection:
            connection.execute("INSERT INTO checks (id, files) VALUES (?, ?)", (check_id, json.dumps(files, ensure_ascii=False)))
        return check_id

    def get(self, check_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM checks WHERE id=?", (check_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        for key in JSON_FIELDS:
            data[key] = json.loads(data[key])
        data["approved"] = bool(data["approved"])
        return data

    def update(self, check_id: str, **fields):
        if not fields or not set(fields) <= MUTABLE_FIELDS:
            raise ValueError("Неизвестное поле проверки")
        values = [json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v for k, v in fields.items()]
        with self.connection() as connection:
            connection.execute(
                "UPDATE checks SET " + ", ".join(f"{key}=?" for key in fields) + " WHERE id=?",
                [*values, check_id],
            )

    def claim(self, check_id: str, stage: str) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE checks SET status='queued', stage=?, error=NULL WHERE id=? AND status NOT IN ('queued','running')",
                (stage, check_id),
            )
            return cursor.rowcount == 1
