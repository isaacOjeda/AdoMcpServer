import logging
import os
import sys
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Final

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError

from ado_mcp.client import ado_client
from ado_mcp.config import Settings
from ado_mcp.errors import AdoApiError, ConfigurationError
from ado_mcp.models import (
    JsonObject,
    JsonValue,
    PullRequestCreate,
    PullRequestThreadCreate,
    WorkItemCreate,
    WorkItemUpdate,
)

mcp: Final = MCPServer("ado-mcp")
_MAX_PAGE_SIZE: Final = 200
_LOGGER = logging.getLogger("ado_mcp")


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


@mcp.tool()
@_tool_error_boundary
async def ado_work_item_create(request: WorkItemCreate) -> JsonObject:
    """Create a work item when write mode is enabled."""
    settings = _settings()
    _write_allowed(settings)
    fields: JsonObject = {
        "/fields/System.Title": request.title,
        **{f"/fields/{key}": value for key, value in request.fields.items()},
    }
    if request.description is not None:
        fields["/fields/System.Description"] = request.description
    async with ado_client(settings) as client:
        payload: JsonValue = [
            {"op": "add", "path": key, "value": value} for key, value in fields.items()
        ]
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


@mcp.tool()
@_tool_error_boundary
async def ado_pull_request_thread_add(request: PullRequestThreadCreate) -> JsonObject:
    """Add a general review comment thread to a pull request."""
    settings = _settings()
    _write_allowed(settings)
    payload: JsonValue = {
        "comments": [{"content": request.content, "parentCommentId": 0}],
        "status": 1,
    }
    async with ado_client(settings) as client:
        return await client.request(
            "POST",
            (
                f"/{client.project(request.project)}/_apis/git/repositories/"
                f"{client.segment(request.repository, 'repository')}"
                f"/pullrequests/{request.pull_request_id}/threads"
            ),
            "add pull request thread",
            payload=payload,
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
