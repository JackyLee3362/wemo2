"""SQLite 存储：采集记录、动态、修订及身份索引。"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from wemo2.deduplicator import canonical_json
from wemo2.interfaces import BaseStore
from wemo2.models import Action, Content, Decision, Observation, Post, SavedObservation


class SQLiteStore(BaseStore):
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

    def close(self) -> None:
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

    def get_observation(
        self, scope: str, observation_id: str
    ) -> SavedObservation | None:
        row = self.connection.execute(
            "SELECT * FROM observations WHERE account_scope=? AND observation_id=?",
            (scope, observation_id),
        ).fetchone()
        if row is None:
            return None
        return SavedObservation.model_validate(dict(row))

    def find_post(self, scope: str, identity_key: str) -> Post | None:
        row = self.connection.execute(
            "SELECT * FROM posts WHERE account_scope=? AND identity_key=?",
            (scope, identity_key),
        ).fetchone()
        if row is None:
            return None
        return self._post_from_row(row)

    def save(self, observation: Observation, decision: Decision) -> Action:
        if decision.action == "unchanged":
            return "unchanged"
        scope = observation.account_scope
        post_id = decision.post_id or str(uuid4())
        incoming = observation.content.model_dump(mode="json", exclude_unset=True)
        observed_at = observation.observed_at.isoformat()
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
                    observed_at,
                    observed_at,
                ),
            )
        else:
            merged = json.loads(existing["content"])
            statuses = observation.field_status
            # 最小实现按顶层字段更新；部分值只补缺，完整值才允许替换。
            for key, value in incoming.items():
                status = statuses.get(key, "partial")
                if value is None or status == "unknown":
                    continue
                if status == "complete" or merged.get(key) is None:
                    merged[key] = value
            content = canonical_json(Content.model_validate(merged))
            action = "unchanged"
            revision = existing["revision"]
            if content != existing["content"]:
                action = "merged"
                revision += 1
            last_seen_at = existing["last_seen_at"]
            if observation.observed_at > datetime.fromisoformat(last_seen_at):
                last_seen_at = observed_at
            self.connection.execute(
                "UPDATE posts SET content=?, revision=?, last_seen_at=? WHERE post_id=?",
                (
                    content,
                    revision,
                    last_seen_at,
                    post_id,
                ),
            )
        if action != "unchanged":
            self.connection.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?)",
                (post_id, revision, content, observation.observation_id),
            )
        self.connection.execute(
            "INSERT INTO observations VALUES (?, ?, ?, ?)",
            (
                scope,
                observation.observation_id,
                post_id,
                canonical_json(observation),
            ),
        )
        return action

    @staticmethod
    def _post_from_row(row: sqlite3.Row) -> Post:
        values = dict(row)
        values["content"] = json.loads(row["content"])
        return Post.model_validate(values)

    def list_posts(self) -> list[Post]:
        rows = self.connection.execute(
            "SELECT * FROM posts ORDER BY first_seen_at, post_id"
        )
        posts = []
        for row in rows:
            posts.append(self._post_from_row(row))
        return posts
