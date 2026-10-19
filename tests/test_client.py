import json

import pytest

from atera_triage.client import AteraClient, AteraError


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode() if payload is not None else b""


def page(items, number, total):
    return 200, {"items": items, "page": number, "totalPages": total}


def test_paginates_until_last_page():
    transport = FakeTransport([page([{"AgentID": 1}], 1, 2), page([{"AgentID": 2}], 2, 2)])
    agents = AteraClient("k", transport=transport).agents()
    assert [a["AgentID"] for a in agents] == [1, 2]
    assert "page=2" in transport.calls[1][1] and "itemsInPage=50" in transport.calls[1][1]
    assert transport.calls[0][2]["X-API-KEY"] == "k"


def test_status_filter_is_passed_and_none_is_dropped():
    transport = FakeTransport([page([], 1, 1), page([], 1, 1)])
    client = AteraClient("k", transport=transport)
    client.tickets("Open")
    client.tickets()
    assert "ticketStatus=Open" in transport.calls[0][1]
    assert "ticketStatus" not in transport.calls[1][1]


def test_retries_after_rate_limit():
    waits = []
    transport = FakeTransport([(429, None), (429, None), page([{"AlertID": 9}], 1, 1)])
    alerts = AteraClient("k", transport=transport, sleep=waits.append).alerts()
    assert alerts == [{"AlertID": 9}]
    assert waits == [5, 10]


def test_gives_up_after_repeated_rate_limits():
    transport = FakeTransport([(429, None)] * 4)
    with pytest.raises(AteraError, match="429"):
        AteraClient("k", transport=transport, sleep=lambda s: None).alerts()


def test_bad_key_error_never_contains_the_key():
    transport = FakeTransport([(401, {"message": "nope"})])
    with pytest.raises(AteraError) as err:
        AteraClient("secret-key-123", transport=transport).ticket(5)
    assert "secret-key-123" not in str(err.value)


def test_update_ticket_posts_json():
    transport = FakeTransport([(200, {"ActionID": 1})])
    AteraClient("k", transport=transport).update_ticket(7, {"TicketStatus": "Resolved"})
    method, url, headers, body = transport.calls[0]
    assert method == "POST" and url.endswith("/tickets/7")
    assert json.loads(body) == {"TicketStatus": "Resolved"}


def test_requires_a_key():
    with pytest.raises(ValueError):
        AteraClient("")


def test_alerts_asks_for_open_only():
    transport = FakeTransport([page([], 1, 1)])
    AteraClient("k", transport=transport).alerts()
    assert "alertStatus=Open" in transport.calls[0][1]
