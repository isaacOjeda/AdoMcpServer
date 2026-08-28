from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)
type JsonObject = dict[str, JsonValue]


class WorkItemCreate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    work_item_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    fields: JsonObject = Field(default_factory=dict)


class PatchOperation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    op: Literal["add", "replace", "remove"]
    path: str = Field(pattern=r"^/fields/[A-Za-z0-9_.-]+$")
    value: JsonValue = None


class WorkItemUpdate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    work_item_id: int = Field(ge=1)
    operations: list[PatchOperation] = Field(min_length=1)


class PullRequestCreate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    repository: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    is_draft: bool = False


class PullRequestThreadCreate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    repository: str = Field(min_length=1)
    pull_request_id: int = Field(ge=1)
    content: str = Field(min_length=1)
