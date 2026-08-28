from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final, Self, final
from urllib.parse import quote

import httpx2
from pydantic import TypeAdapter

from ado_mcp.config import Settings
from ado_mcp.errors import AdoApiError, ConfigurationError
from ado_mcp.models import JsonObject, JsonValue

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from ado_mcp.config import Settings

type QueryParams = dict[str, str | int | bool]

_DEFAULT_TIMEOUT: Final = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
_HTTP_SUCCESS_MIN: Final = 200
_HTTP_SUCCESS_MAX: Final = 300
_DEFAULT_LIMITS: Final = httpx2.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)
_JSON_OBJECT_ADAPTER: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)
_LOGGER = logging.getLogger("ado_mcp")


def _segment(value: str, label: str) -> str:
    if not value.strip():
        raise ConfigurationError(f"{label} must not be empty")
    return quote(value, safe="")


@asynccontextmanager
async def ado_client(settings: Settings) -> AsyncGenerator[AdoClient, None]:
    async with AdoClient(settings) as client:
        yield client


@final
class AdoClient:
    _client: httpx2.AsyncClient
    _api_version: str
    _default_project: str | None
    read_only: bool

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        organization = _segment(settings.organization, "organization")
        request_transport = transport or httpx2.AsyncHTTPTransport(
            http2=True,
            retries=3,
            limits=_DEFAULT_LIMITS,
        )
        self._client = httpx2.AsyncClient(
            base_url=f"https://dev.azure.com/{organization}",
            auth=("", settings.pat.get_secret_value()),
            headers={"Accept": "application/json"},
            follow_redirects=False,
            timeout=_DEFAULT_TIMEOUT,
            transport=request_transport,
        )
        self._api_version = settings.api_version
        self._default_project = settings.default_project
        self.read_only = settings.read_only

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    def project(self, value: str | None) -> str:
        project = value or self._default_project
        if project is None:
            raise ConfigurationError("project is required")
        return _segment(project, "project")

    def segment(self, value: str, label: str) -> str:
        return _segment(value, label)

    async def request(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        params: QueryParams | None = None,
        payload: JsonValue | None = None,
        content_type: str | None = None,
    ) -> JsonObject:
        headers = {"Content-Type": content_type} if content_type else None
        _LOGGER.info(
            "ado_request_started method=%s operation=%s path=%s",
            method,
            operation,
            path,
        )
        try:
            response = await self._client.request(
                method,
                path,
                params={"api-version": self._api_version, **(params or {})},
                json=payload,
                headers=headers,
            )
        except httpx2.HTTPError as error:
            _LOGGER.exception(
                "ado_request_transport_failed operation=%s error_type=%s",
                operation,
                type(error).__name__,
            )
            raise AdoApiError(0, operation) from error
        if not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
            _LOGGER.warning(
                "ado_request_failed operation=%s status_code=%s",
                operation,
                response.status_code,
            )
            raise AdoApiError(response.status_code, operation)
        _LOGGER.info(
            "ado_request_completed operation=%s status_code=%s",
            operation,
            response.status_code,
        )
        try:
            return _JSON_OBJECT_ADAPTER.validate_json(response.content)
        except ValueError as error:
            raise AdoApiError(502, operation) from error

    async def get_work_item(self, project: str | None, work_item_id: int) -> JsonObject:
        return await self.request(
            "GET",
            f"/{self.project(project)}/_apis/wit/workitems/{work_item_id}",
            "get work item",
            params={"$expand": "All"},
        )
