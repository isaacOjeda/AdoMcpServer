import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from ado_mcp.models import WorkItemCreate
from ado_mcp.server import ado_work_item_create, mcp


def test_server_exposes_required_tool_groups() -> None:
    tools = {tool.name for tool in anyio.run(mcp.list_tools)}

    assert {
        "ado_work_item_get",
        "ado_work_items_query",
        "ado_work_item_create",
        "ado_work_item_update",
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
