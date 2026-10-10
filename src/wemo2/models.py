"""组件间的 Pydantic 数据契约；JSON 用于导入和持久化。"""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    model_validator,
)

# ID 必须是含非空白字符的字符串，避免隐式把数字转换为源身份。
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]
# unknown=未观察到，partial=不完整，complete=完整；用于判断字段能否替换。
FieldStatus = Literal["unknown", "partial", "complete"]
# created=新动态，merged=内容修订，unchanged=重复或无内容变化。
Action = Literal["created", "merged", "unchanged"]


class DataModel(BaseModel):
    """采集数据基类：允许 JSON 扩展字段，并校验赋值及非有限数值。"""

    model_config = ConfigDict(
        extra="allow", validate_assignment=True, allow_inf_nan=False
    )
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class Source(DataModel):
    """采集来源及源动态身份；稳定 ID 必须同时提供命名空间。"""

    platform: Identifier = "wechat_moments"
    method: Literal["vision", "ui", "database", "import"] = "import"
    instance_id: Identifier | None = None  # 设备或数据源实例，用于追踪来源。
    native_post_id: Identifier | None = None  # 微信侧动态 ID，不是内部 post_id。
    native_id_namespace: Identifier | None = None  # 源 ID 的有效范围。
    native_post_id_stable: StrictBool = False  # 只有稳定源 ID 才能用于身份去重。

    @model_validator(mode="after")
    def require_stable_identity(self) -> Self:
        if self.native_post_id_stable and (
            not self.native_post_id or not self.native_id_namespace
        ):
            raise ValueError("稳定源 ID 需要 native_post_id 和 native_id_namespace")
        return self


class Author(DataModel):
    """作者身份；昵称仅用于展示，不能作为唯一身份。"""

    author_id: Identifier | None = None
    native_user_id: Identifier | None = None
    native_user_id_namespace: Identifier | None = None
    native_user_id_stable: StrictBool = False
    display_name: str | None = None


class PublishedTime(DataModel):
    """模糊发布时间：保留原文及推断区间，区间右端不包含。"""

    raw: str | None = None
    precision: str | None = None
    earliest: AwareDatetime | None = None
    latest_exclusive: AwareDatetime | None = None


class Media(DataModel):
    """采集到的附件元数据；source_ref 可能过期，不等于已备份文件。"""

    attachment_key: Identifier | None = None
    kind: Literal["image", "video", "audio", "file"]
    position: int = Field(default=0, ge=0, strict=True)
    source_asset_id: Identifier | None = None
    source_ref: str | None = None
    rendition: str | None = None  # 如 thumbnail/original，不能将缩略图冒充原图。
    mime_type: str | None = None
    sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )  # 实际字节哈希。


class Comment(DataModel):
    """评论及回复关系；没有稳定源身份时不凭相同文本合并。"""

    comment_id: Identifier | None = None
    native_comment_id: Identifier | None = None
    author: Author | None = None
    text: str | None = None
    published_at: AwareDatetime | None = None
    reply_to: Identifier | None = None


class Reaction(DataModel):
    """点赞等互动；type 表示互动类型，actor 表示参与者。"""

    reaction_id: Identifier | None = None
    type: str
    actor: Author | None = None


class Content(DataModel):
    """动态内容快照；None/未提供表示未知，空列表是否完整由 field_status 指定。"""

    author: Author | None = None
    published_at: AwareDatetime | None = None  # 仅保存已知的精确发布时间。
    published_time: PublishedTime | None = None
    text: str | None = None
    location: JsonValue = None
    link: JsonValue = None
    # 缺失与明确空列表不同，序列化时使用 exclude_unset。
    media: list[Media] | None = None
    comments: list[Comment] | None = None
    reactions: list[Reaction] | None = None


class Observation(DataModel):
    """一次采集事实；重试复用 observation_id，新观察使用新 ID。"""

    schema_version: Literal["0.1.0"]
    observation_id: Identifier
    account_scope: Identifier  # 备份账号命名空间，默认不跨账号合并。
    collector_id: Identifier | None = None
    run_id: Identifier | None = None
    observed_at: AwareDatetime  # 采集时间，与动态发布时间不同，必须含时区。
    source: Source
    content: Content
    field_status: dict[str, FieldStatus] = Field(
        default_factory=dict
    )  # 顶层内容字段完整度。
    evidence: list[dict[str, JsonValue]] = Field(
        default_factory=list
    )  # 截图等证据引用。
    extensions: dict[str, JsonValue] = Field(default_factory=dict)  # 采集器专属信息。


class Decision(BaseModel):
    """只读去重决定；由存储器执行写入并返回实际动作。"""

    model_config = ConfigDict(frozen=True)
    action: Action
    post_id: Identifier | None = None  # 匹配到的内部动态 ID；新建时由存储器分配。
    identity_key: str | None = None  # 去重策略生成的源身份键，不是正文哈希。


class SavedObservation(BaseModel):
    """已保存采集记录的索引项，payload 用于检测幂等请求中的数据冲突。"""

    account_scope: Identifier
    observation_id: Identifier
    post_id: Identifier
    payload: str


class Post(BaseModel):
    """合并后的内部动态；多条 Observation 可以关联同一条 Post。"""

    post_id: Identifier
    account_scope: Identifier
    identity_key: str | None = None
    content: Content
    revision: int = Field(ge=1)  # 内容更新时递增，单纯重放不会增加版本。
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime


class RunResult(BaseModel):
    """一次协调器执行的统计；collected 等于三个动作计数之和。"""

    model_config = ConfigDict(validate_assignment=True)
    collected: int = Field(default=0, ge=0)
    created: int = Field(default=0, ge=0)
    merged: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
