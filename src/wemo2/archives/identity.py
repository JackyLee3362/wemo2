"""本地朋友圈身份键规则，供归档合并及旧布局升级共用。"""

import hashlib
import json
import unicodedata

from wemo2.common.names import normalize_person


def moment_key(person: str | None, text: str | None, text_status: str) -> str | None:
    """按人物和完整文案生成身份键；未知和截断正文不参与本地归档合并。"""
    person = normalize_person(person)
    if person is None or text is None or text_status != "complete":
        return None
    person = " ".join(unicodedata.normalize("NFC", person).split())
    text = " ".join(unicodedata.normalize("NFC", text).split())
    payload = json.dumps([person, text], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
