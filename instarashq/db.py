"""طبقة تخزين SQLite: أهداف، إجراءات، حصص، حالة تشغيل."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    pk              TEXT PRIMARY KEY,
    username        TEXT NOT NULL,
    full_name       TEXT,
    source          TEXT NOT NULL,
    follower_count  INTEGER,
    following_count INTEGER,
    media_count     INTEGER,
    is_private      INTEGER NOT NULL DEFAULT 0,
    is_verified     INTEGER NOT NULL DEFAULT 0,
    is_business     INTEGER NOT NULL DEFAULT 0,
    has_profile_pic INTEGER NOT NULL DEFAULT 0,
    language        TEXT,
    score           REAL NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'new',
    harvested_at    TEXT NOT NULL,
    enriched_at     TEXT,
    followed_at     TEXT,
    unfollowed_at   TEXT,
    last_action_at  TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_targets_state ON targets(state, score DESC, harvested_at);
CREATE INDEX IF NOT EXISTS idx_targets_followed ON targets(state, followed_at);
CREATE INDEX IF NOT EXISTS idx_targets_username ON targets(username);

CREATE TABLE IF NOT EXISTS actions (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    pk     TEXT,
    action TEXT NOT NULL,
    ok     INTEGER NOT NULL,
    error  TEXT,
    meta   TEXT,
    ts     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts);
CREATE INDEX IF NOT EXISTS idx_actions_action ON actions(action, ts);

CREATE TABLE IF NOT EXISTS quota (
    day    TEXT NOT NULL,
    action TEXT NOT NULL,
    used   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, action)
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    stats      TEXT
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """مغلّف SQLite آمن للخيوط مع معاملات صريحة."""

    def __init__(self, path: str) -> None:
        self.path = str(Path(path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO kv(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # ---------- kv ----------
    def kv_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---------- targets ----------
    def add_targets(self, rows: Iterable[Dict[str, Any]]) -> int:
        """يُدرج أهدافاً جديدة ويتجاهل المكرر. يُرجع عدد المُدرج."""
        inserted = 0
        with self.transaction() as conn:
            for row in rows:
                cur = conn.execute(
                    """
                    INSERT INTO targets (pk, username, full_name, source, follower_count, following_count,
                                         media_count, is_private, is_verified, is_business, has_profile_pic,
                                         language, score, state, harvested_at)
                    VALUES (:pk, :username, :full_name, :source, :follower_count, :following_count,
                            :media_count, :is_private, :is_verified, :is_business, :has_profile_pic,
                            :language, :score, :state, :harvested_at)
                    ON CONFLICT(pk) DO NOTHING
                    """,
                    row,
                )
                inserted += cur.rowcount
        return inserted

    def get_target(self, pk: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute("SELECT * FROM targets WHERE pk = ?", (pk,)).fetchone()

    def target_by_username(self, username: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM targets WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()

    def next_target(self) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM targets WHERE state = 'new' ORDER BY score DESC, harvested_at ASC LIMIT 1"
            ).fetchone()

    def pool_size(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM targets WHERE state = 'new'").fetchone()
        return int(row["c"])

    def set_state(self, pk: str, state: str, note: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE targets SET state = ?, note = COALESCE(?, note), last_action_at = ? WHERE pk = ?",
                (state, note, _utcnow(), pk),
            )

    def mark_followed(self, pk: str) -> None:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "UPDATE targets SET state = 'followed', followed_at = ?, last_action_at = ?, attempts = attempts + 1 WHERE pk = ?",
                (now, now, pk),
            )

    def mark_unfollowed(self, pk: str) -> None:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "UPDATE targets SET state = 'unfollowed', unfollowed_at = ?, last_action_at = ? WHERE pk = ?",
                (now, now, pk),
            )

    def bump_attempts(self, pk: str, note: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE targets SET attempts = attempts + 1, last_action_at = ?, note = COALESCE(?, note) WHERE pk = ?",
                (_utcnow(), note, pk),
            )

    def update_enrichment(self, pk: str, data: Dict[str, Any]) -> None:
        fields = {
            "full_name": data.get("full_name"),
            "follower_count": data.get("follower_count"),
            "following_count": data.get("following_count"),
            "media_count": data.get("media_count"),
            "is_private": int(bool(data.get("is_private"))),
            "is_verified": int(bool(data.get("is_verified"))),
            "is_business": int(bool(data.get("is_business"))),
            "has_profile_pic": int(bool(data.get("has_profile_pic"))),
            "language": data.get("language"),
            "enriched_at": _utcnow(),
        }
        with self._lock:
            self._conn.execute(
                """
                UPDATE targets SET full_name = :full_name, follower_count = :follower_count,
                    following_count = :following_count, media_count = :media_count,
                    is_private = :is_private, is_verified = :is_verified, is_business = :is_business,
                    has_profile_pic = :has_profile_pic, language = :language, enriched_at = :enriched_at
                WHERE pk = :pk
                """,
                {**fields, "pk": pk},
            )

    def due_unfollows(self, min_age_hours: int, limit: int, whitelist: Sequence[str]) -> List[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat(timespec="seconds")
        placeholders = ",".join("?" for _ in whitelist) if whitelist else "''"
        params: List[Any] = [cutoff]
        params.extend(whitelist)
        params.append(limit)
        sql = f"""
            SELECT * FROM targets
            WHERE state = 'followed' AND followed_at IS NOT NULL AND followed_at <= ?
              AND username NOT IN ({placeholders})
            ORDER BY followed_at ASC LIMIT ?
        """
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def count_state(self, state: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM targets WHERE state = ?", (state,)).fetchone()
        return int(row["c"])

    def following_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM targets WHERE state = 'followed'").fetchone()
        return int(row["c"])

    # ---------- actions ----------
    def record_action(
        self, pk: Optional[str], action: str, ok: bool, error: Optional[str] = None, meta: Optional[Dict[str, Any]] = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO actions(pk, action, ok, error, meta, ts) VALUES(?, ?, ?, ?, ?, ?)",
                (pk, action, int(ok), error, json.dumps(meta, ensure_ascii=False) if meta else None, _utcnow()),
            )

    def recent_actions(self, limit: int = 50) -> List[sqlite3.Row]:
        with self._lock:
            return list(
                self._conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            )

    def action_counts_since(self, action: str, since_iso: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM actions WHERE action = ? AND ok = 1 AND ts >= ?",
                (action, since_iso),
            ).fetchone()
        return int(row["c"])

    # ---------- quota ----------
    def quota_used(self, day: str, action: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT used FROM quota WHERE day = ? AND action = ?", (day, action)
            ).fetchone()
        return int(row["used"]) if row else 0

    def quota_bump(self, day: str, action: str, amount: int = 1) -> int:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO quota(day, action, used) VALUES(?, ?, ?)
                ON CONFLICT(day, action) DO UPDATE SET used = used + excluded.used
                """,
                (day, action, amount),
            )
            row = self._conn.execute("SELECT used FROM quota WHERE day = ? AND action = ?", (day, action)).fetchone()
        return int(row["used"])

    # ---------- runs ----------
    def start_run(self) -> int:
        with self._lock:
            cur = self._conn.execute("INSERT INTO runs(started_at) VALUES(?)", (_utcnow(),))
        return int(cur.lastrowid)

    def end_run(self, run_id: int, stats: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET ended_at = ?, stats = ? WHERE id = ?",
                (_utcnow(), json.dumps(stats, ensure_ascii=False), run_id),
            )

    # ---------- export ----------
    def export_targets(self, state: Optional[str] = None) -> List[sqlite3.Row]:
        with self._lock:
            if state:
                return list(self._conn.execute("SELECT * FROM targets WHERE state = ? ORDER BY harvested_at", (state,)))
            return list(self._conn.execute("SELECT * FROM targets ORDER BY harvested_at"))

    def stats(self) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT state, COUNT(*) AS c FROM targets GROUP BY state").fetchall()
        out = {row["state"]: int(row["c"]) for row in rows}
        out["total"] = sum(out.values())
        return out
