import logging
import os
import sys
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Final, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError

from ado_mcp.client import AdoClient, ado_client
from ado_mcp.config import Settings
from ado_mcp.errors import AdoApiError, ConfigurationError
from ado_mcp.models import (
    JsonObject,
    JsonValue,
    PullRequestCreate,
    PullRequestThreadCreate,
    PullRequestThreadReply,
    PullRequestThreadStatusUpdate,
    ThreadStatus,
    WorkItemCreate,
    WorkItemLinkAdd,
    WorkItemUpdate,
)

mcp: Final = MCPServer("ado-mcp")
_MAX_PAGE_SIZE: Final = 200
_LOGGER = logging.getLogger("ado_mcp")
_LINK_TYPE_TO_RELATION: Final[dict[str, str]] = {
    "parent": "System.LinkTypes.Hierarchy-Reverse",
    "child": "System.LinkTypes.Hierarchy-Forward",
    "related": "System.LinkTypes.Related",
}
_THREAD_STATUS_TO_CODE: Final[dict[ThreadStatus, int]] = {
    "active": 1,
    "fixed": 2,
    "wontFix": 3,
    "closed": 4,
    "byDesign": 5,
    "pending": 6,
}
_TEXT_COMMENT_TYPE: Final = 1


def _tool_error_boundary[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await function(*args, **kwargs)
        except (ConfigurationError, AdoApiError) as error:
            raise ToolError(str(error)) from error

    return wrapped


def _settings() -> Settings:
    values: dict[str, str | None] = {
        "organization": os.environ.get("ADO_ORGANIZATION"),
        "pat": os.environ.get("ADO_PAT"),
        "default_project": os.environ.get("ADO_DEFAULT_PROJECT"),
        "read_only": os.environ.get("ADO_READ_ONLY"),
        "api_version": os.environ.get("ADO_API_VERSION"),
    }
    try:
        return Settings.model_validate(
            {key: value for key, value in values.items() if value is not None}
        )
    except ValidationError as error:
        fields = ", ".join(
            f"ADO_{str(item['loc'][0]).upper()}"
            for item in error.errors()
            if item["type"] == "missing"
        )
        _LOGGER.warning("ado_configuration_invalid missing=%s", fields)
        raise ConfigurationError(f"missing required configuration: {fields}") from error


def _write_allowed(settings: Settings) -> None:
    if settings.read_only:
        raise ConfigurationError("write operation disabled while ADO_READ_ONLY=true")


def relation_patch_op(
    url: str, link_type: Literal["parent", "child", "related"], comment: str | None
) -> JsonObject:
    relation: JsonObject = {"rel": _LINK_TYPE_TO_RELATION[link_type], "url": url}
    if comment is not None:
        relation["attributes"] = {"comment": comment}
    return {"op": "add", "path": "/relations/-", "value": relation}


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_get(
    work_item_id: int, project: str | None = None
) -> JsonObject:
    """Get a work item and its fields, relations, and revision."""
    async with ado_client(_settings()) as client:
        return await client.get_work_item(project, work_item_id)


@mcp.tool()
@_tool_error_boundary
async def ado_work_items_query(
    wiql: str, project: str | None = None, top: int = 50
) -> JsonObject:
    """Run a bounded WIQL query in a project."""
    if not wiql.strip():
        raise ConfigurationError("wiql must not be empty")
    if not 1 <= top <= _MAX_PAGE_SIZE:
        raise ConfigurationError("top must be between 1 and 200")
    async with ado_client(_settings()) as client:
        return await client.request(
            "POST",
            f"/{client.project(project)}/_apis/wit/wiql",
            "query work items",
            params={"$top": top},
            payload={"query": wiql},
        )


def work_item_create_payload(
    request: WorkItemCreate, parent_url: str | None
) -> JsonValue:
    fields: JsonObject = {
        "/fields/System.Title": request.title,
        **{f"/fields/{key}": value for key, value in request.fields.items()},
    }
    if request.description is not None:
        fields["/fields/System.Description"] = request.description
    payload: JsonValue = [
        {"op": "add", "path": key, "value": value} for key, value in fields.items()
    ]
    if parent_url is not None:
        payload = [*payload, relation_patch_op(parent_url, "parent", None)]
    return payload


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_create(request: WorkItemCreate) -> JsonObject:
    """Create a work item when write mode is enabled."""
    settings = _settings()
    _write_allowed(settings)
    async with ado_client(settings) as client:
        parent_url = (
            client.work_item_url(request.parent_id)
            if request.parent_id is not None
            else None
        )
        payload = work_item_create_payload(request, parent_url)
        return await client.request(
            "POST",
            f"/{client.project(request.project)}/_apis/wit/workitems/${request.work_item_type}",
            "create work item",
            payload=payload,
            content_type="application/json-patch+json",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_update(request: WorkItemUpdate) -> JsonObject:
    """Update explicitly selected work item fields using JSON Patch."""
    settings = _settings()
    _write_allowed(settings)
    payload: JsonValue = TypeAdapter[JsonValue](JsonValue).validate_python(
        [operation.model_dump(exclude_none=True) for operation in request.operations]
    )
    async with ado_client(settings) as client:
        return await client.request(
            "PATCH",
            f"/{client.project(request.project)}/_apis/wit/workitems/{request.work_item_id}",
            "update work item",
            payload=payload,
            content_type="application/json-patch+json",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_link_add(request: WorkItemLinkAdd) -> JsonObject:
    """Add a parent, child, or related work item link when write mode is enabled."""
    settings = _settings()
    _write_allowed(settings)
    if request.work_item_id == request.target_work_item_id:
        raise ConfigurationError("work_item_id and target_work_item_id must differ")
    async with ado_client(settings) as client:
        payload: JsonValue = [
            relation_patch_op(
                client.work_item_url(request.target_work_item_id),
                request.link_type,
                request.comment,
            )
        ]
        return await client.request(
            "PATCH",
            f"/{client.project(request.project)}/_apis/wit/workitems/{request.work_item_id}",
            "add work item link",
            payload=payload,
            content_type="application/json-patch+json",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_types_list(project: str | None = None) -> JsonObject:
    """List work item types available in a project."""
    async with ado_client(_settings()) as client:
        return await client.request(
            "GET",
            f"/{client.project(project)}/_apis/wit/workitemtypes",
            "list work item types",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_fields_list(project: str | None = None) -> JsonObject:
    """List work item fields available in a project."""
    async with ado_client(_settings()) as client:
        return await client.request(
            "GET",
            f"/{client.project(project)}/_apis/wit/fields",
            "list work item fields",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_type_get(
    work_item_type: str, project: str | None = None
) -> JsonObject:
    """Get fields, states, and rules for one work item type."""
    async with ado_client(_settings()) as client:
        return await client.request(
            "GET",
            (
                f"/{client.project(project)}/_apis/wit/workitemtypes/"
                f"{client.segment(work_item_type, 'work item type')}"
            ),
            "get work item type",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_comments_list(
    work_item_id: int, project: str | None = None, top: int = 50
) -> JsonObject:
    """List comments for a work item."""
    if not 1 <= top <= _MAX_PAGE_SIZE:
        raise ConfigurationError("top must be between 1 and 200")
    async with ado_client(_settings()) as client:
        return await client.request(
            "GET",
            f"/{client.project(project)}/_apis/wit/workItems/{work_item_id}/comments",
            "list work item comments",
            params={"$top": top, "api-version": "7.2-preview.4"},
        )


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_comment_add(
    work_item_id: int, text: str, project: str | None = None
) -> JsonObject:
    """Add a comment to a work item when write mode is enabled."""
    settings = _settings()
    _write_allowed(settings)
    if not text.strip():
        raise ConfigurationError("text must not be empty")
    async with ado_client(settings) as client:
        return await client.request(
            "POST",
            f"/{client.project(project)}/_apis/wit/workItems/{work_item_id}/comments",
            "add work item comment",
            params={"api-version": "7.2-preview.4"},
            payload={"text": text},
        )


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_get(
    repository: str,
    pull_request_id: int,
    project: str | None = None,
) -> JsonObject:
    """Get a pull request by repository and ID."""
    async with ado_client(_settings()) as client:
        return await client.request(
            "GET",
            (
                f"/{client.project(project)}/_apis/git/repositories/"
                f"{client.segment(repository, 'repository')}"
                f"/pullrequests/{pull_request_id}"
            ),
            "get pull request",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_threads_list(
    repository: str,
    pull_request_id: int,
    project: str | None = None,
) -> JsonObject:
    """List review threads and comments for a pull request."""
    async with ado_client(_settings()) as client:
        return await client.request(
            "GET",
            (
                f"/{client.project(project)}/_apis/git/repositories/"
                f"{client.segment(repository, 'repository')}"
                f"/pullrequests/{pull_request_id}/threads"
            ),
            "list pull request threads",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_pull_requests_list(
    project: str | None = None, repository: str | None = None, top: int = 50
) -> JsonObject:
    """List pull requests in a project or repository."""
    if not 1 <= top <= _MAX_PAGE_SIZE:
        raise ConfigurationError("top must be between 1 and 200")
    async with ado_client(_settings()) as client:
        if repository:
            repo = client.segment(repository, "repository")
            path = (
                f"/{client.project(project)}/_apis/git/repositories/{repo}/pullrequests"
            )
        else:
            path = f"/{client.project(project)}/_apis/git/pullrequests"
        return await client.request(
            "GET",
            path,
            "list pull requests",
            params={"$top": top},
        )


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_changes(
    repository: str, pull_request_id: int, project: str | None = None
) -> JsonObject:
    """List changed files for a pull request."""
    async with ado_client(_settings()) as client:
        return await client.request(
            "GET",
            (
                f"/{client.project(project)}/_apis/git/repositories/"
                f"{client.segment(repository, 'repository')}"
                f"/pullrequests/{pull_request_id}/changes"
            ),
            "list pull request changes",
        )


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_create(request: PullRequestCreate) -> JsonObject:
    """Create a pull request when write mode is enabled."""
    settings = _settings()
    _write_allowed(settings)
    payload: JsonValue = {
        "sourceRefName": request.source_ref,
        "targetRefName": request.target_ref,
        "title": request.title,
        "description": request.description,
        "isDraft": request.is_draft,
    }
    async with ado_client(settings) as client:
        return await client.request(
            "POST",
            (
                f"/{client.project(request.project)}/_apis/git/repositories/"
                f"{client.segment(request.repository, 'repository')}"
                "/pullrequests"
            ),
            "create pull request",
            payload=payload,
        )


def _thread_path(
    client: AdoClient, project: str | None, repository: str, pull_request_id: int
) -> str:
    return (
        f"/{client.project(project)}/_apis/git/repositories/"
        f"{client.segment(repository, 'repository')}"
        f"/pullrequests/{pull_request_id}/threads"
    )


def thread_create_payload(request: PullRequestThreadCreate) -> JsonValue:
    content = request.content
    if request.suggestion is not None:
        content = f"{content}\n\n```suggestion\n{request.suggestion}\n```"
    payload: JsonObject = {
        "comments": [
            {
                "content": content,
                "parentCommentId": 0,
                "commentType": _TEXT_COMMENT_TYPE,
            }
        ],
        "status": _THREAD_STATUS_TO_CODE[request.status],
    }
    if request.file_path is not None:
        context: JsonObject = {"filePath": f"/{request.file_path.lstrip('/')}"}
        if request.start_line is not None:
            end_line = request.end_line or request.start_line
            context[f"{request.side}FileStart"] = {
                "line": request.start_line,
                "offset": request.start_offset,
            }
            context[f"{request.side}FileEnd"] = {
                "line": end_line,
                "offset": request.end_offset,
            }
        payload["threadContext"] = context
    return payload


def thread_reply_payload(request: PullRequestThreadReply) -> JsonValue:
    return {
        "content": request.content,
        "parentCommentId": request.parent_comment_id,
        "commentType": _TEXT_COMMENT_TYPE,
    }


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_thread_add(request: PullRequestThreadCreate) -> JsonObject:
    """Add a review comment thread to a pull request.

    Without file_path the thread is a general PR comment. With file_path it is
    attached to that file; add start_line (and optionally end_line) to anchor it
    to lines on the right (new) or left (old) side of the diff. A suggestion is
    rendered as an Azure DevOps suggested change on the right side.
    """
    settings = _settings()
    _write_allowed(settings)
    payload = thread_create_payload(request)
    async with ado_client(settings) as client:
        return await client.request(
            "POST",
            _thread_path(
                client, request.project, request.repository, request.pull_request_id
            ),
            "add pull request thread",
            payload=payload,
        )


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_thread_reply(request: PullRequestThreadReply) -> JsonObject:
    """Reply to an existing pull request thread when write mode is enabled."""
    settings = _settings()
    _write_allowed(settings)
    async with ado_client(settings) as client:
        return await client.request(
            "POST",
            (
                _thread_path(
                    client, request.project, request.repository, request.pull_request_id
                )
                + f"/{request.thread_id}/comments"
            ),
            "reply to pull request thread",
            payload=thread_reply_payload(request),
        )


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_thread_status_update(
    request: PullRequestThreadStatusUpdate,
) -> JsonObject:
    """Resolve, close, or reactivate a pull request thread."""
    settings = _settings()
    _write_allowed(settings)
    async with ado_client(settings) as client:
        return await client.request(
            "PATCH",
            (
                _thread_path(
                    client, request.project, request.repository, request.pull_request_id
                )
                + f"/{request.thread_id}"
            ),
            "update pull request thread status",
            payload={"status": _THREAD_STATUS_TO_CODE[request.status]},
        )


async def _run() -> None:
    await mcp.run_stdio_async()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        _ = anyio.run(_run)
    except ConfigurationError as error:
        _ = sys.stderr.write(f"{error}\n")
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
