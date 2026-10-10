"""保守去重：Observation 幂等与稳定源身份，不按正文误合并。"""

import json

from pydantic import BaseModel

from wemo2.interfaces import BaseDeduplicator, BaseStore
from wemo2.models import Decision, Observation


def canonical_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_unset=True)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class Deduplicator(BaseDeduplicator):
    def resolve(self, observation: Observation, index: BaseStore) -> Decision:
        scope = observation.account_scope
        previous = index.get_observation(scope, observation.observation_id)
        if previous is not None:
            # 兼容旧数据库中的时间字符串格式。
            if canonical_json(
                Observation.model_validate_json(previous.payload)
            ) != canonical_json(observation):
                raise ValueError(
                    "IDEMPOTENCY_CONFLICT: 同一 Observation ID 携带不同数据"
                )
            return Decision(action="unchanged", post_id=previous.post_id)

        source = observation.source
        identity = None
        if source.native_post_id_stable:
            identity = canonical_json(
                [
                    source.platform,
                    source.native_id_namespace,
                    source.native_post_id,
                ]
            )
        existing = index.find_post(scope, identity) if identity is not None else None
        if existing is None:
            return Decision(action="created", identity_key=identity)
        return Decision(
            action="merged", post_id=existing.post_id, identity_key=identity
        )
