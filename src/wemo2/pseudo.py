"""流程演示组件：内置数据、最小幂等策略和内存存储。"""

from collections.abc import Iterable
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from wemo2.deduplicator import canonical_json
from wemo2.interfaces import BaseCollector, BaseDeduplicator, BaseStore
from wemo2.models import (
    Action,
    Content,
    Decision,
    Observation,
    Post,
    SavedObservation,
    Source,
)
from wemo2_collector.models import Platform


class PseudoCollector(BaseCollector):
    def __init__(
        self,
        observations: Iterable[Observation] | None = None,
        platform: Platform = Platform.windows,
    ):
        self.platform = Platform(platform)
        if observations is None:
            first = Observation(
                schema_version="0.1.0",
                observation_id="pseudo-obs-001",
                account_scope="pseudo-account",
                collector_id="pseudo",
                observed_at=datetime(2026, 10, 10, 13, tzinfo=UTC),
                source=Source(
                    native_post_id="pseudo-post-001",
                    native_id_namespace="pseudo-account",
                    native_post_id_stable=True,
                ),
                content=Content(text="第一条模拟朋友圈"),
                field_status={"text": "complete"},
            )
            second = first.model_copy(deep=True)
            second.observation_id = "pseudo-obs-002"
            second.source.native_post_id = "pseudo-post-002"
            second.content.text = "第二条模拟朋友圈"
            observations = [first, first, second]
        records = []
        for observation in observations:
            records.append(observation.model_copy(deep=True))
        self.observations = tuple(records)

    def collect(self) -> Iterable[Observation]:
        for observation in self.observations:
            yield observation.model_copy(deep=True)


class PseudoDeduplicator(BaseDeduplicator):
    """仅演示 Observation 幂等，不判断不同观察是否属于同一条动态。"""

    def resolve(self, observation: Observation, store: BaseStore) -> Decision:
        previous = store.get_observation(
            observation.account_scope, observation.observation_id
        )
        if previous is not None:
            previous_observation = Observation.model_validate_json(previous.payload)
            previous_payload = canonical_json(previous_observation)
            current_payload = canonical_json(observation)
            if previous_payload != current_payload:
                raise ValueError(
                    "IDEMPOTENCY_CONFLICT: 同一 Observation ID 携带不同数据"
                )
            return Decision(
                action="unchanged", post_id=previous.post_id, is_duplicate=True
            )
        return Decision(action="created")


class PseudoStore(BaseStore):
    """单线程内存演示存储；退出进程即丢失，不是持久化备份。"""

    def __init__(self):
        self._posts: dict[str, Post] = {}
        self._observations: dict[tuple[str, str], SavedObservation] = {}
        self._revisions: dict[tuple[str, int], Content] = {}

    @contextmanager
    def transaction(self):
        snapshot = deepcopy((self._posts, self._observations, self._revisions))
        try:
            yield
        except BaseException:
            self._posts, self._observations, self._revisions = snapshot
            raise

    def get_observation(
        self, scope: str, observation_id: str
    ) -> SavedObservation | None:
        record = self._observations.get((scope, observation_id))
        if record is None:
            return None
        return record.model_copy(deep=True)

    def find_post(self, scope: str, identity_key: str) -> Post | None:
        for post in self._posts.values():
            if post.account_scope == scope and post.identity_key == identity_key:
                return post.model_copy(deep=True)
        return None

    def save(self, observation: Observation, decision: Decision) -> Action:
        if decision.action == "unchanged":
            return "unchanged"
        key = (observation.account_scope, observation.observation_id)
        if key in self._observations:
            raise ValueError("采集记录已经存在，必须先去重")
        post_id = decision.post_id or str(uuid4())
        existing = self._posts.get(post_id)
        if existing is None:
            if decision.identity_key is not None:
                duplicate = self.find_post(
                    observation.account_scope, decision.identity_key
                )
                if duplicate is not None:
                    raise ValueError("动态身份已经存在，必须先去重")
            post = Post(
                post_id=post_id,
                account_scope=observation.account_scope,
                identity_key=decision.identity_key,
                content=observation.content.model_copy(deep=True),
                revision=1,
                first_seen_at=observation.observed_at,
                last_seen_at=observation.observed_at,
            )
            action: Action = "created"
        else:
            post = existing.model_copy(deep=True)
            merged = post.content.model_dump(mode="json", exclude_unset=True)
            incoming = observation.content.model_dump(mode="json", exclude_unset=True)
            for field, value in incoming.items():
                status = observation.field_status.get(field, "partial")
                if value is None or status == "unknown":
                    continue
                if status == "complete" or merged.get(field) is None:
                    merged[field] = value
            content = Content.model_validate(merged)
            changed = canonical_json(content) != canonical_json(post.content)
            post.content = content
            action = "unchanged"
            if changed:
                post.revision += 1
                action = "merged"
            post.last_seen_at = max(post.last_seen_at, observation.observed_at)
        self._posts[post_id] = post
        if action != "unchanged":
            self._revisions[(post_id, post.revision)] = post.content.model_copy(
                deep=True
            )
        self._observations[key] = SavedObservation(
            account_scope=observation.account_scope,
            observation_id=observation.observation_id,
            post_id=post_id,
            payload=canonical_json(observation),
        )
        return action

    def list_posts(self) -> list[Post]:
        posts = []
        for post in self._posts.values():
            posts.append(post.model_copy(deep=True))
        return posts

    def close(self) -> None:
        pass
