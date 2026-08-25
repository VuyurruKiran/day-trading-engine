from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.request import Request, urlopen


def get_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", **dict(headers or {})})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS provider URLs.
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("provider response must be a JSON object")
    return payload
