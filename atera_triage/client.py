"""A small, dependency-free client for the Atera REST API (v3).

Only the calls the triage tools need are implemented. The HTTP layer is a
single injectable function so tests never touch the network.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

BASE_URL = "https://app.atera.com/api/v3"
PAGE_SIZE = 50  # the largest page Atera allows
MAX_PAGES = 40  # safety stop: 2,000 items per list call

# (method, url, headers, body) -> (status, body bytes)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class AteraError(Exception):
    """An Atera API call failed. The message never contains the API key."""


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class AteraClient:
    def __init__(self, api_key: str, transport: Transport = urllib_transport,
                 base_url: str = BASE_URL, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ValueError("An Atera API key is required")
        self._key = api_key
        self._transport = transport
        self._base = base_url.rstrip("/")
        self._sleep = sleep

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"X-API-KEY": self._key, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        for attempt in range(4):
            status, raw = self._transport(method, url, headers, data)
            if status == 429 and attempt < 3:  # rate limited (700 requests/minute)
                self._sleep(2 ** attempt * 5)
                continue
            break
        if status == 401:
            raise AteraError("Atera rejected the API key (401). Check ATERA_API_KEY.")
        if status >= 400:
            raise AteraError(f"Atera returned HTTP {status} for {method} {path}")
        return json.loads(raw) if raw else None

    def _list(self, path: str, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            data = self._request("GET", path, {**params, "page": page, "itemsInPage": PAGE_SIZE})
            items.extend(data.get("items", []))
            if page >= data.get("totalPages", 1):
                break
        return items

    # --- read ---
    def tickets(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._list("/tickets", ticketStatus=status)

    def ticket(self, ticket_id: int) -> dict[str, Any]:
        return self._request("GET", f"/tickets/{int(ticket_id)}")

    def ticket_comments(self, ticket_id: int) -> list[dict[str, Any]]:
        return self._list(f"/tickets/{int(ticket_id)}/comments")

    def agents(self) -> list[dict[str, Any]]:
        return self._list("/agents")

    def alerts(self) -> list[dict[str, Any]]:
        # Accounts keep years of archived alerts; ask only for open ones.
        return self._list("/alerts", alertStatus="Open")

    # --- write (only reachable when writes are enabled) ---
    def update_ticket(self, ticket_id: int, fields: dict[str, Any]) -> Any:
        return self._request("POST", f"/tickets/{int(ticket_id)}", body=fields)
