from typing import Literal

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent
from pydantic import ValidationError

from ado_mcp.models import (
    PullRequestThreadCreate,
    PullRequestThreadReply,
    PullRequestThreadStatusUpdate,
    WorkItemCreate,
    WorkItemLinkAdd,
)
from ado_mcp.server import (
    ado_pull_request_thread_reply,
    ado_pull_request_thread_status_update,
    ado_work_item_create,
    ado_work_item_link_add,
    mcp,
    relation_patch_op,
    thread_create_payload,
    thread_reply_payload,
    work_item_create_payload,
)


def test_server_exposes_required_tool_groups() -> None:
    tools = {tool.name for tool in anyio.run(mcp.list_tools)}

    assert {
        "ado_work_item_get",
        "ado_work_items_query",
        "ado_work_item_create",
        "ado_work_item_update",
        "ado_work_item_link_add",
        "ado_work_item_comments_list",
        "ado_work_item_comment_add",
        "ado_work_item_types_list",
        "ado_work_item_type_get",
        "ado_work_item_fields_list",
        "ado_pull_requests_list",
        "ado_pull_request_get",
        "ado_pull_request_changes",
        "ado_pull_request_create",
        "ado_pull_request_threads_list",
        "ado_pull_request_thread_add",
    } <= tools


@pytest.mark.anyio
async def test_mutation_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADO_ORGANIZATION", "contoso")
    monkeypatch.setenv("ADO_PAT", "secret-pat")

    with pytest.raises(ToolError, match="ADO_READ_ONLY"):
        _ = await ado_work_item_create(
            WorkItemCreate(work_item_type="Task", title="blocked")
        )


@pytest.mark.anyio
async def test_mcp_returns_configuration_detail_for_missing_settings(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("ADO_ORGANIZATION", raising=False)
    monkeypatch.delenv("ADO_PAT", raising=False)

    async with Client(mcp) as client:
        result = await client.call_tool("ado_work_item_get", {"work_item_id": 123})

    assert result.is_error is True
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert "ADO_ORGANIZATION" in content.text
    assert "ADO_PAT" in content.text
    assert "ADO_ORGANIZATION" in caplog.text
    assert "ADO_PAT" in caplog.text
    assert "secret-pat" not in caplog.text


@pytest.mark.anyio
async def test_work_item_link_add_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADO_ORGANIZATION", "contoso")
    monkeypatch.setenv("ADO_PAT", "secret-pat")

    with pytest.raises(ToolError, match="ADO_READ_ONLY"):
        _ = await ado_work_item_link_add(
            WorkItemLinkAdd(work_item_id=1, target_work_item_id=2)
        )


@pytest.mark.anyio
async def test_work_item_link_add_rejects_linking_to_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADO_ORGANIZATION", "contoso")
    monkeypatch.setenv("ADO_PAT", "secret-pat")
    monkeypatch.setenv("ADO_READ_ONLY", "false")

    with pytest.raises(ToolError, match="must differ"):
        _ = await ado_work_item_link_add(
            WorkItemLinkAdd(work_item_id=1, target_work_item_id=1)
        )


@pytest.mark.parametrize(
    ("link_type", "expected_rel"),
    [
        ("parent", "System.LinkTypes.Hierarchy-Reverse"),
        ("child", "System.LinkTypes.Hierarchy-Forward"),
        ("related", "System.LinkTypes.Related"),
    ],
)
def test_relation_patch_op_maps_link_type_to_relation(
    link_type: Literal["parent", "child", "related"], expected_rel: str
) -> None:
    op = relation_patch_op(
        "https://dev.azure.com/contoso/_apis/wit/workItems/7", link_type, None
    )

    assert op == {
        "op": "add",
        "path": "/relations/-",
        "value": {
            "rel": expected_rel,
            "url": "https://dev.azure.com/contoso/_apis/wit/workItems/7",
        },
    }


def test_relation_patch_op_includes_comment_when_provided() -> None:
    op = relation_patch_op(
        "https://dev.azure.com/contoso/_apis/wit/workItems/7",
        "related",
        "linked from triage",
    )

    assert op["value"] == {
        "rel": "System.LinkTypes.Related",
        "url": "https://dev.azure.com/contoso/_apis/wit/workItems/7",
        "attributes": {"comment": "linked from triage"},
    }


def test_relation_patch_op_omits_attributes_without_comment() -> None:
    op = relation_patch_op(
        "https://dev.azure.com/contoso/_apis/wit/workItems/7", "parent", None
    )

    value = op["value"]
    assert isinstance(value, dict)
    assert "attributes" not in value


def test_work_item_link_add_validation_rejects_invalid_ids() -> None:
    with pytest.raises(ValidationError):
        _ = WorkItemLinkAdd(work_item_id=0, target_work_item_id=2)
    with pytest.raises(ValidationError):
        _ = WorkItemLinkAdd(work_item_id=1, target_work_item_id=0)


def test_work_item_link_add_validation_rejects_invalid_link_type() -> None:
    with pytest.raises(ValidationError):
        _ = WorkItemLinkAdd.model_validate(
            {"work_item_id": 1, "target_work_item_id": 2, "link_type": "sibling"}
        )


def test_work_item_create_payload_appends_parent_relation_op() -> None:
    request = WorkItemCreate(work_item_type="Task", title="child task", parent_id=9)

    payload = work_item_create_payload(
        request, "https://dev.azure.com/contoso/_apis/wit/workItems/9"
    )

    assert isinstance(payload, list)
    assert payload[-1] == {
        "op": "add",
        "path": "/relations/-",
        "value": {
            "rel": "System.LinkTypes.Hierarchy-Reverse",
            "url": "https://dev.azure.com/contoso/_apis/wit/workItems/9",
        },
    }


def test_work_item_create_payload_has_no_relation_op_without_parent() -> None:
    request = WorkItemCreate(work_item_type="Task", title="standalone task")

    payload = work_item_create_payload(request, None)

    assert isinstance(payload, list)
    assert all(
        item.get("path") != "/relations/-" for item in payload if isinstance(item, dict)
    )


def test_server_exposes_pull_request_thread_tools() -> None:
    tools = {tool.name for tool in anyio.run(mcp.list_tools)}

    assert {
        "ado_pull_request_thread_reply",
        "ado_pull_request_thread_status_update",
    } <= tools


def test_thread_create_payload_without_anchor_keeps_general_comment() -> None:
    request = PullRequestThreadCreate(
        repository="repo", pull_request_id=1, content="looks good"
    )

    assert thread_create_payload(request) == {
        "comments": [{"content": "looks good", "parentCommentId": 0, "commentType": 1}],
        "status": 1,
    }


def test_thread_create_payload_anchors_to_right_side_lines() -> None:
    request = PullRequestThreadCreate(
        repository="repo",
        pull_request_id=1,
        content="rename this",
        file_path="src/app.py",
        start_line=10,
        end_line=12,
    )

    payload = thread_create_payload(request)

    assert isinstance(payload, dict)
    assert payload["threadContext"] == {
        "filePath": "/src/app.py",
        "rightFileStart": {"line": 10, "offset": 1},
        "rightFileEnd": {"line": 12, "offset": 1},
    }


def test_thread_create_payload_anchors_single_line_on_left_side() -> None:
    request = PullRequestThreadCreate(
        repository="repo",
        pull_request_id=1,
        content="why removed?",
        file_path="/src/app.py",
        start_line=4,
        start_offset=3,
        end_offset=9,
        side="left",
    )

    payload = thread_create_payload(request)

    assert isinstance(payload, dict)
    assert payload["threadContext"] == {
        "filePath": "/src/app.py",
        "leftFileStart": {"line": 4, "offset": 3},
        "leftFileEnd": {"line": 4, "offset": 9},
    }


def test_thread_create_payload_supports_file_level_comment() -> None:
    request = PullRequestThreadCreate(
        repository="repo", pull_request_id=1, content="split file", file_path="a.py"
    )

    payload = thread_create_payload(request)

    assert isinstance(payload, dict)
    assert payload["threadContext"] == {"filePath": "/a.py"}


def test_thread_create_payload_maps_status() -> None:
    request = PullRequestThreadCreate(
        repository="repo", pull_request_id=1, content="fyi", status="closed"
    )

    payload = thread_create_payload(request)

    assert isinstance(payload, dict)
    assert payload["status"] == 4


def test_thread_create_payload_appends_suggestion_block() -> None:
    request = PullRequestThreadCreate(
        repository="repo",
        pull_request_id=1,
        content="prefer f-string",
        file_path="a.py",
        start_line=3,
        suggestion='name = f"{first} {last}"',
    )

    payload = thread_create_payload(request)

    assert isinstance(payload, dict)
    assert payload["comments"] == [
        {
            "content": (
                'prefer f-string\n\n```suggestion\nname = f"{first} {last}"\n```'
            ),
            "parentCommentId": 0,
            "commentType": 1,
        }
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {"start_line": 3},
        {"file_path": "a.py", "end_line": 3},
        {"file_path": "a.py", "start_line": 5, "end_line": 4},
        {"file_path": "a.py", "start_line": 0},
        {"file_path": " "},
        {"suggestion": "x"},
        {"file_path": "a.py", "suggestion": "x"},
        {"file_path": "a.py", "start_line": 1, "side": "left", "suggestion": "x"},
        {"file_path": "a.py", "start_offset": 2},
    ],
)
def test_thread_create_validation_rejects_inconsistent_anchor(
    fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _ = PullRequestThreadCreate.model_validate(
            {"repository": "repo", "pull_request_id": 1, "content": "c", **fields}
        )


def test_thread_reply_payload_targets_parent_comment() -> None:
    request = PullRequestThreadReply(
        repository="repo", pull_request_id=1, thread_id=7, content="done"
    )

    assert thread_reply_payload(request) == {
        "content": "done",
        "parentCommentId": 1,
        "commentType": 1,
    }


def test_thread_reply_validation_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        _ = PullRequestThreadReply(
            repository="repo", pull_request_id=1, thread_id=0, content="x"
        )
    with pytest.raises(ValidationError):
        _ = PullRequestThreadReply(
            repository="repo", pull_request_id=1, thread_id=1, content=""
        )


@pytest.mark.anyio
async def test_pull_request_thread_writes_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADO_ORGANIZATION", "contoso")
    monkeypatch.setenv("ADO_PAT", "secret-pat")

    with pytest.raises(ToolError, match="ADO_READ_ONLY"):
        _ = await ado_pull_request_thread_reply(
            PullRequestThreadReply(
                repository="repo", pull_request_id=1, thread_id=1, content="x"
            )
        )
    with pytest.raises(ToolError, match="ADO_READ_ONLY"):
        _ = await ado_pull_request_thread_status_update(
            PullRequestThreadStatusUpdate(
                repository="repo", pull_request_id=1, thread_id=1, status="fixed"
            )
        )
