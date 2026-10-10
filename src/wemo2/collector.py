"""最小采集器：读取符合架构契约的 JSON Observation 数组。"""

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

Observation = dict[str, Any]


class Collector(Protocol):
    def collect(self) -> Iterable[Observation]: ...


class JsonCollector:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def collect(self) -> list[Observation]:
        records = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(records, list):
            raise ValueError("采集文件必须是 JSON 数组")
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"第 {index + 1} 条记录必须是对象")
            if record.get("schema_version") != "0.1.0":
                raise ValueError(f"第 {index + 1} 条记录的 schema_version 必须为 0.1.0")
            for key in ("observation_id", "account_scope", "observed_at"):
                if not isinstance(record.get(key), str) or not record[key].strip():
                    raise ValueError(f"第 {index + 1} 条记录缺少字符串字段 {key}")
            if not isinstance(record.get("source"), dict) or not isinstance(
                record.get("content"), dict
            ):
                raise ValueError(f"第 {index + 1} 条记录需要 source 和 content 对象")
            source = record["source"]
            if datetime.fromisoformat(record["observed_at"]).tzinfo is None:
                raise ValueError("observed_at 必须包含时区")
            statuses = record.get("field_status", {})
            if not isinstance(statuses, dict) or any(
                value not in ("unknown", "partial", "complete")
                for value in statuses.values()
            ):
                raise ValueError("field_status 必须使用 unknown、partial 或 complete")
            if source.get("native_post_id_stable") is True:
                for key in ("platform", "native_id_namespace", "native_post_id"):
                    if not isinstance(source.get(key), str) or not source[key].strip():
                        raise ValueError(f"稳定源 ID 需要字符串字段 source.{key}")
        return records
