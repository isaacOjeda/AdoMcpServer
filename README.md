# Azure DevOps MCP

Local, stdio-based MCP server for Azure DevOps and Claude Desktop.

## Requirements

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- Azure DevOps PAT with only the scopes required by the enabled operations

## Configuration

The server reads these environment variables:

```text
ADO_ORGANIZATION=your-organization
ADO_PAT=your-pat
ADO_DEFAULT_PROJECT=your-project
ADO_READ_ONLY=true
ADO_API_VERSION=7.1
```

Keep `ADO_READ_ONLY=true` until read operations are verified. Never commit the PAT or put it in an MCP tool argument.

## Work item links

`ado_work_item_create` accepts an optional `parent_id` to link the new work item to a
parent as part of creation. `ado_work_item_link_add` adds a `parent`, `child`, or
`related` link between two existing work items. Both require `ADO_READ_ONLY=false`.

## Run locally

```bash
uv sync
uv run ado-mcp
```

## Claude Desktop

Add the following entry to Claude Desktop's `claude_desktop_config.json`, using absolute paths:

```json
{
  "mcpServers": {
    "azure-devops": {
      "command": "/absolute/path/to/ado-mcp/.venv/bin/ado-mcp",
      "env": {
        "ADO_ORGANIZATION": "your-organization",
        "ADO_PAT": "your-pat",
        "ADO_DEFAULT_PROJECT": "your-project",
        "ADO_READ_ONLY": "true"
      }
    }
  }
}
```

Restart Claude Desktop after changing the configuration. The server writes diagnostics only to stderr so stdout remains available for MCP protocol traffic.

On macOS, Claude Desktop captures this server's stderr in:

```text
~/Library/Logs/Claude/mcp-server-azure-devops.log
```

Look for `ado_request_failed` and `status_code` when Azure DevOps rejects a request.
