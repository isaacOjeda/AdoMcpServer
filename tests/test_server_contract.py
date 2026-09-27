from typing import Literal

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent
from pydantic import ValidationError

from ado_mcp.models import WorkItemCreate, WorkItemLinkAdd
from ado_mcp.server import (
    ado_work_item_create,
    ado_work_item_link_add,
    mcp,
    relation_patch_op,
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
