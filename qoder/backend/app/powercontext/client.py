"""HTTP client for the PowerContext Server.

This is the only module in StudyFlow that talks to PowerContext. Every call goes
through :class:`PowerContextClient`, which centralises authentication, timeouts,
retries and error translation so the service layer above stays free of transport
details.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

LOGGER = logging.getLogger("studyflow.powercontext")

FAMILY_ATOMIC_MEMORY = "atomic-memory"


class PowerContextError(RuntimeError):
    """Base class for every failure raised by the PowerContext client."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class PowerContextUnavailable(PowerContextError):
    """The Server could not be reached or returned a server-side failure."""


class PowerContextTimeout(PowerContextError):
    """The Server did not answer within the configured timeout."""


class PowerContextAuthError(PowerContextError):
    """The Server rejected the credential."""


class PowerContextRequestError(PowerContextError):
    """The Server rejected the request payload."""


@dataclass(slots=True)
class MemoryEntry:
    """One Atomic Memory record as returned by PowerContext."""

    artifact_id: str
    revision: int
    kind: str
    text: str
    state: str
    state_version: int = 0
    family: str = FAMILY_ATOMIC_MEMORY

    @property
    def etag(self) -> str:
        """The ``If-Match`` token describing the revision we last observed."""
        return f'"revision:{self.revision}"'


@dataclass(slots=True)
class ScopeInfo:
    """A PowerContext Scope descriptor."""

    scope_id: str
    title: str
    summary: str
    version: int = 1


class PowerContextClient:
    """Async client for the subset of the PowerContext HTTP API StudyFlow uses."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._max_retries = max_retries
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers=headers,
        )

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> PowerContextClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # --- transport ------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Send one request, retrying transient failures, and translate transport errors."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json_body,
                    headers=headers,
                    params=params,
                )
            except httpx.TimeoutException as error:
                last_error = PowerContextTimeout(
                    f"PowerContext 请求超时（{self._base_url}{path}，{self._timeout}s 超时）"
                )
                LOGGER.warning("powercontext timeout path=%s attempt=%s", path, attempt + 1)
            except httpx.HTTPError as error:
                last_error = PowerContextUnavailable(
                    f"无法连接 PowerContext Server（{self._base_url}{path}）：{error}"
                )
                LOGGER.warning("powercontext unreachable path=%s attempt=%s", path, attempt + 1)
            else:
                if response.status_code < 400:
                    return response
                detail = _extract_error(response)
                if response.status_code in (401, 403):
                    raise PowerContextAuthError(
                        f"PowerContext 拒绝了当前凭据（HTTP {response.status_code}）：{detail}",
                        status_code=response.status_code,
                        detail=detail,
                    )
                if response.status_code >= 500:
                    last_error = PowerContextUnavailable(
                        f"PowerContext Server 暂时不可用（HTTP {response.status_code}）：{detail}",
                        status_code=response.status_code,
                        detail=detail,
                    )
                    LOGGER.warning("powercontext 5xx path=%s attempt=%s", path, attempt + 1)
                else:
                    raise PowerContextRequestError(
                        f"PowerContext 拒绝了请求（HTTP {response.status_code}）：{detail}",
                        status_code=response.status_code,
                        detail=detail,
                    )
            if attempt < self._max_retries:
                await asyncio.sleep(0.4 * (attempt + 1))
        assert last_error is not None
        raise last_error

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("POST", path, json_body=body)
        return response.json()

    # --- health ---------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Return the Server readiness payload."""
        response = await self._request("GET", "/health/ready")
        return response.json()

    async def server_info(self) -> dict[str, Any]:
        """Return the Server identity and supported feature contracts."""
        response = await self._request("GET", "/v1/server-info")
        return response.json()

    # --- scopes ---------------------------------------------------------

    async def create_scope(
        self,
        *,
        title: str,
        summary: str,
        external_references: list[dict[str, str]],
        idempotency_key: str,
    ) -> ScopeInfo:
        """Create an independent Scope and return its descriptor."""
        body = {
            "title": title,
            "summary": summary,
            "external_references": external_references,
            "idempotency_key": idempotency_key,
        }
        payload = await self._post_json("/v1/scopes", body)
        return ScopeInfo(
            scope_id=payload["scope_id"],
            title=payload.get("title", title),
            summary=payload.get("summary", summary),
            version=payload.get("version", 1),
        )

    async def find_scope_by_external_reference(
        self,
        *,
        kind: str,
        value: str,
    ) -> ScopeInfo | None:
        """Find a Scope by one of its external references."""
        response = await self._request(
            "GET",
            "/v1/scopes",
            params={
                "external_reference_kind": kind,
                "query": value,
                "query_field": "external_reference_value",
            },
        )
        items = response.json().get("items", [])
        if not items:
            return None
        first = items[0]
        return ScopeInfo(
            scope_id=first["scope_id"],
            title=first.get("title", ""),
            summary=first.get("summary", ""),
            version=first.get("version", 1),
        )

    async def get_scope(self, scope_id: str) -> ScopeInfo:
        """Read one Scope descriptor."""
        response = await self._request("GET", f"/v1/scopes/{scope_id}")
        payload = response.json()
        return ScopeInfo(
            scope_id=payload["scope_id"],
            title=payload.get("title", ""),
            summary=payload.get("summary", ""),
            version=payload.get("version", 1),
        )

    async def set_scope_binding(
        self,
        *,
        integration: str,
        kind: str,
        external_id: str,
        scope_id: str,
    ) -> None:
        """Persist an external identity to Scope binding."""
        body = {
            "key": {"integration": integration, "kind": kind, "external_id": external_id},
            "scope_id": scope_id,
        }
        await self._request("PUT", "/v1/scope-bindings", json_body=body)

    async def resolve_scope_binding(
        self,
        *,
        integration: str,
        kind: str,
        external_id: str,
    ) -> ScopeInfo | None:
        """Resolve the Scope bound to an external identity."""
        body = {
            "allow_default": False,
            "binding_keys": [
                {"integration": integration, "kind": kind, "external_id": external_id}
            ],
        }
        try:
            payload = await self._post_json("/v1/scope-bindings/resolve", body)
        except PowerContextRequestError as error:
            if error.status_code == 404:
                return None
            raise
        return ScopeInfo(
            scope_id=payload["scope_id"],
            title=payload.get("title", ""),
            summary=payload.get("summary", ""),
            version=payload.get("version", 1),
        )

    # --- memory ---------------------------------------------------------

    async def remember(
        self,
        *,
        scope_id: str,
        kind: str,
        text: str,
        reason: str | None = None,
    ) -> list[MemoryEntry]:
        """Write one Atomic Memory entry and return the stored records."""
        body: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            body["reason"] = reason
        payload = await self._post_json("/v1/memory/remember", body)
        return [_to_entry(record) for record in payload.get("records", [])]

    async def list_memory_entries(
        self,
        *,
        scope_id: str,
        limit: int = 100,
        include_inactive: bool = False,
    ) -> list[MemoryEntry]:
        """List the Atomic Memory entries of one Scope."""
        body = {
            "scope_id": scope_id,
            "limit": limit,
            "include_inactive": include_inactive,
        }
        payload = await self._post_json("/v1/memory/entries/list", body)
        return [_to_entry(record) for record in payload.get("entries", [])]

    async def search_memory(
        self,
        *,
        scope_id: str,
        query: str,
        limit: int = 10,
    ) -> list[tuple[MemoryEntry, float]]:
        """Search active Atomic Memory and return the ranked hits."""
        body = {"scope_id": scope_id, "query": query, "limit": limit}
        payload = await self._post_json("/v1/memory/search", body)
        hits: list[tuple[MemoryEntry, float]] = []
        for hit in payload.get("hits", []):
            memory = hit.get("memory")
            if not memory:
                continue
            hits.append((_to_entry(memory), float(hit.get("score", 0.0))))
        return hits

    async def get_artifact(self, *, scope_id: str, artifact_id: str) -> tuple[str, str]:
        """Read one Atomic Memory artifact and return its ``(text, etag)``."""
        response = await self._request(
            "GET",
            f"/v1/scopes/{scope_id}/artifacts/{FAMILY_ATOMIC_MEMORY}/{artifact_id}",
        )
        payload = response.json()
        content = payload.get("content") or {}
        etag = response.headers.get("etag", f'"revision:{payload.get("revision", 1)}"')
        return str(content.get("text", "")), etag

    async def revise_artifact(
        self,
        *,
        scope_id: str,
        artifact_id: str,
        kind: str,
        text: str,
        if_match: str,
    ) -> int:
        """Replace an Atomic Memory artifact, creating a new revision.

        Returns the new revision number.
        """
        body = {"content": {"kind": kind, "text": text}}
        response = await self._request(
            "PUT",
            f"/v1/scopes/{scope_id}/artifacts/{FAMILY_ATOMIC_MEMORY}/{artifact_id}",
            json_body=body,
            headers={"If-Match": if_match},
        )
        return int(response.json().get("revision", 0))


def _to_entry(record: dict[str, Any]) -> MemoryEntry:
    artifact = record.get("artifact") or {}
    return MemoryEntry(
        artifact_id=artifact.get("artifact_id", ""),
        revision=int(artifact.get("revision", 1)),
        kind=record.get("kind", ""),
        text=record.get("text", ""),
        state=record.get("state", "active"),
        state_version=int(record.get("state_version", 0)),
        family=artifact.get("family", FAMILY_ATOMIC_MEMORY),
    )


def _extract_error(response: httpx.Response) -> str:
    """Pull a human-readable message out of a PowerContext error payload."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:400]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or ""
        details = error.get("details")
        if details:
            return f"{message} ({details})"
        return str(message)
    return str(payload)[:400]


__all__ = [
    "FAMILY_ATOMIC_MEMORY",
    "MemoryEntry",
    "PowerContextAuthError",
    "PowerContextClient",
    "PowerContextError",
    "PowerContextRequestError",
    "PowerContextTimeout",
    "PowerContextUnavailable",
    "ScopeInfo",
]
