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

Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]
FieldStatus = Literal["unknown", "partial", "complete"]
Action = Literal["created", "merged", "unchanged"]


class DataModel(BaseModel):
    model_config = ConfigDict(
        extra="allow", validate_assignment=True, allow_inf_nan=False
    )
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class Source(DataModel):
    platform: Identifier = "wechat_moments"
    method: Literal["vision", "ui", "database", "import"] = "import"
    instance_id: Identifier | None = None
    native_post_id: Identifier | None = None
    native_id_namespace: Identifier | None = None
    native_post_id_stable: StrictBool = False

    @model_validator(mode="after")
    def require_stable_identity(self) -> Self:
        if self.native_post_id_stable and (
            not self.native_post_id or not self.native_id_namespace
        ):
            raise ValueError("稳定源 ID 需要 native_post_id 和 native_id_namespace")
        return self


class Author(DataModel):
    author_id: Identifier | None = None
    native_user_id: Identifier | None = None
    native_user_id_namespace: Identifier | None = None
    native_user_id_stable: StrictBool = False
    display_name: str | None = None


class PublishedTime(DataModel):
    raw: str | None = None
    precision: str | None = None
    earliest: AwareDatetime | None = None
    latest_exclusive: AwareDatetime | None = None


class Media(DataModel):
    attachment_key: Identifier | None = None
    kind: Literal["image", "video", "audio", "file"]
    position: int = Field(default=0, ge=0, strict=True)
    source_asset_id: Identifier | None = None
    source_ref: str | None = None
    rendition: str | None = None
    mime_type: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class Comment(DataModel):
    comment_id: Identifier | None = None
    native_comment_id: Identifier | None = None
    author: Author | None = None
    text: str | None = None
    published_at: AwareDatetime | None = None
    reply_to: Identifier | None = None


class Reaction(DataModel):
    reaction_id: Identifier | None = None
    type: str
    actor: Author | None = None


class Content(DataModel):
    author: Author | None = None
    published_at: AwareDatetime | None = None
    published_time: PublishedTime | None = None
    text: str | None = None
    location: JsonValue = None
    link: JsonValue = None
    # 缺失与明确空列表不同，序列化时使用 exclude_unset。
    media: list[Media] | None = None
    comments: list[Comment] | None = None
    reactions: list[Reaction] | None = None


class Observation(DataModel):
    schema_version: Literal["0.1.0"]
    observation_id: Identifier
    account_scope: Identifier
    collector_id: Identifier | None = None
    run_id: Identifier | None = None
    observed_at: AwareDatetime
    source: Source
    content: Content
    field_status: dict[str, FieldStatus] = Field(default_factory=dict)
    evidence: list[dict[str, JsonValue]] = Field(default_factory=list)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: Action
    post_id: Identifier | None = None
    identity_key: str | None = None


class SavedObservation(BaseModel):
    account_scope: Identifier
    observation_id: Identifier
    post_id: Identifier
    payload: str


class Post(BaseModel):
    post_id: Identifier
    account_scope: Identifier
    identity_key: str | None = None
    content: Content
    revision: int = Field(ge=1)
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime


class RunResult(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    collected: int = Field(default=0, ge=0)
    created: int = Field(default=0, ge=0)
    merged: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
