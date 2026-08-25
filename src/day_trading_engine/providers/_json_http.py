from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ProviderHttpError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


def get_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", **dict(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS providers.
            payload = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip() or str(exc.reason)
        raise ProviderHttpError(exc.code, detail) from exc
    if not isinstance(payload, dict):
        raise ValueError("provider response must be a JSON object")
    return payload
