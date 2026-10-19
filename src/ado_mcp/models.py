from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)
type JsonObject = dict[str, JsonValue]
type ThreadStatus = Literal[
    "active", "fixed", "wontFix", "closed", "byDesign", "pending"
]


class WorkItemCreate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    work_item_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    fields: JsonObject = Field(default_factory=dict)
    parent_id: int | None = Field(default=None, ge=1)


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


class WorkItemLinkAdd(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    work_item_id: int = Field(ge=1)
    target_work_item_id: int = Field(ge=1)
    link_type: Literal["parent", "child", "related"] = "parent"
    comment: str | None = None


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
    status: ThreadStatus = "active"
    file_path: (
        Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"[^/]")] | None
    ) = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    start_offset: int = Field(default=1, ge=1)
    end_offset: int = Field(default=1, ge=1)
    side: Literal["right", "left"] = "right"
    suggestion: str | None = None

    @model_validator(mode="after")
    def _check_anchor(self) -> Self:
        has_offsets = "start_offset" in self.model_fields_set or (
            "end_offset" in self.model_fields_set
        )
        if self.file_path is None and (
            self.start_line is not None or self.end_line is not None
        ):
            raise ValueError("line anchors require file_path")
        if self.start_line is None and (self.end_line is not None or has_offsets):
            raise ValueError("end_line and offsets require start_line")
        if (
            self.end_line is not None
            and self.start_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be >= start_line")
        if (
            self.start_line is not None
            and (self.end_line or self.start_line) == self.start_line
            and self.end_offset < self.start_offset
        ):
            raise ValueError("end_offset must be >= start_offset on a single line")
        if self.suggestion is not None and (
            self.start_line is None or self.side != "right"
        ):
            raise ValueError("suggestion requires start_line on the right side")
        return self


class PullRequestThreadReply(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    repository: str = Field(min_length=1)
    pull_request_id: int = Field(ge=1)
    thread_id: int = Field(ge=1)
    content: str = Field(min_length=1)
    parent_comment_id: int = Field(default=1, ge=1)


class PullRequestThreadStatusUpdate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    project: str | None = None
    repository: str = Field(min_length=1)
    pull_request_id: int = Field(ge=1)
    thread_id: int = Field(ge=1)
    status: ThreadStatus
