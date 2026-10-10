"""保守去重：Observation 幂等与稳定源身份，不按正文误合并。"""

import json
from dataclasses import dataclass
from typing import Protocol

from wemo2.collector import Observation


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class DedupIndex(Protocol):
    def get_observation(self, scope: str, observation_id: str) -> dict | None: ...
    def find_post(self, scope: str, identity_key: str) -> dict | None: ...


@dataclass(frozen=True)
class Decision:
    action: str
    post_id: str | None = None
    identity_key: str | None = None


class Deduplicator:
    def resolve(self, observation: Observation, index: DedupIndex) -> Decision:
        scope = observation["account_scope"]
        previous = index.get_observation(scope, observation["observation_id"])
        if previous is not None:
            if previous["payload"] != canonical_json(observation):
                raise ValueError(
                    "IDEMPOTENCY_CONFLICT: 同一 Observation ID 携带不同数据"
                )
            return Decision("unchanged", previous["post_id"])

        source = observation["source"]
        identity = None
        if source.get("native_post_id_stable") is True:
            identity = canonical_json(
                [
                    source["platform"],
                    source["native_id_namespace"],
                    source["native_post_id"],
                ]
            )
        existing = index.find_post(scope, identity) if identity is not None else None
        if existing is None:
            return Decision("created", identity_key=identity)
        return Decision("merged", existing["post_id"], identity)
