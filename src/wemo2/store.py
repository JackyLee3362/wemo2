"""SQLite 存储：采集记录、动态、修订及身份索引。"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from wemo2.collector import Observation
from wemo2.deduplicator import Decision, canonical_json


class SQLiteStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                post_id TEXT PRIMARY KEY, account_scope TEXT NOT NULL,
                identity_key TEXT, content TEXT NOT NULL, revision INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                UNIQUE(account_scope, identity_key)
            );
            CREATE TABLE IF NOT EXISTS observations (
                account_scope TEXT NOT NULL, observation_id TEXT NOT NULL,
                post_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(account_scope, observation_id)
            );
            CREATE TABLE IF NOT EXISTS revisions (
                post_id TEXT NOT NULL, revision INTEGER NOT NULL,
                content TEXT NOT NULL, observation_id TEXT NOT NULL,
                PRIMARY KEY(post_id, revision)
            );
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    @contextmanager
    def transaction(self):
        # 匹配与保存共用写锁，避免并发任务在匹配之后重复创建。
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def get_observation(self, scope: str, observation_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM observations WHERE account_scope=? AND observation_id=?",
            (scope, observation_id),
        ).fetchone()
        return dict(row) if row else None

    def find_post(self, scope: str, identity_key: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM posts WHERE account_scope=? AND identity_key=?",
            (scope, identity_key),
        ).fetchone()
        return dict(row) if row else None

    def save(self, observation: Observation, decision: Decision) -> str:
        if decision.action == "unchanged":
            return "unchanged"
        scope = observation["account_scope"]
        post_id = decision.post_id or str(uuid4())
        incoming = observation["content"]
        existing = self.connection.execute(
            "SELECT * FROM posts WHERE post_id=?", (post_id,)
        ).fetchone()
        if existing is None:
            content = canonical_json(incoming)
            revision = 1
            action = "created"
            self.connection.execute(
                "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    post_id,
                    scope,
                    decision.identity_key,
                    content,
                    revision,
                    observation["observed_at"],
                    observation["observed_at"],
                ),
            )
        else:
            merged = json.loads(existing["content"])
            statuses = observation.get("field_status", {})
            # 最小实现按顶层字段更新；部分值只补缺，完整值才允许替换。
            for key, value in incoming.items():
                status = statuses.get(key, "partial")
                if value is not None and status != "unknown":
                    if statuses.get(key) == "complete" or merged.get(key) is None:
                        merged[key] = value
            content = canonical_json(merged)
            changed = content != existing["content"]
            action = "merged" if changed else "unchanged"
            revision = existing["revision"] + int(changed)
            self.connection.execute(
                "UPDATE posts SET content=?, revision=?, last_seen_at=? WHERE post_id=?",
                (
                    content,
                    revision,
                    max(
                        (existing["last_seen_at"], observation["observed_at"]),
                        key=datetime.fromisoformat,
                    ),
                    post_id,
                ),
            )
        if action != "unchanged":
            self.connection.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?)",
                (post_id, revision, content, observation["observation_id"]),
            )
        self.connection.execute(
            "INSERT INTO observations VALUES (?, ?, ?, ?)",
            (
                scope,
                observation["observation_id"],
                post_id,
                canonical_json(observation),
            ),
        )
        return action

    def list_posts(self) -> list[dict]:
        return [
            dict(row) | {"content": json.loads(row["content"])}
            for row in self.connection.execute(
                "SELECT * FROM posts ORDER BY first_seen_at, post_id"
            )
        ]
