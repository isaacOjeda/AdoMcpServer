import httpx2
import pytest
from pydantic import SecretStr

from ado_mcp.client import AdoClient
from ado_mcp.config import Settings
from ado_mcp.errors import AdoApiError, ConfigurationError


@pytest.mark.anyio
async def test_path_segments_are_encoded() -> None:
    settings = Settings(organization="contoso", pat=SecretStr("secret-pat"))

    async with AdoClient(settings) as client:
        assert client.segment("project/repo", "repository") == "project%2Frepo"


@pytest.mark.anyio
async def test_empty_path_segment_is_rejected() -> None:
    settings = Settings(organization="contoso", pat=SecretStr("secret-pat"))

    async with AdoClient(settings) as client:
        with pytest.raises(ConfigurationError):
            _ = client.segment("", "repository")


def test_api_error_can_receive_a_traceback() -> None:
    error = AdoApiError(401, "get work item")

    error.__traceback__ = None

    assert error.status_code == 401


@pytest.mark.anyio
async def test_api_status_is_preserved_without_following_auth_redirects() -> None:
    settings = Settings(organization="contoso", pat=SecretStr("secret-pat"))

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, request=request, json={"message": "denied"})

    async with AdoClient(settings, transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(AdoApiError, match="HTTP 401"):
            _ = await client.request("GET", "/probe", "probe")
