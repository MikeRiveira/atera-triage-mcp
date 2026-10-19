import asyncio
import datetime as dt
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from atera_triage.demo import DemoClient
from atera_triage.server import build_server

NOW = dt.datetime(2026, 10, 19, 13, 0, tzinfo=dt.timezone.utc)


def make(allow_writes=False):
    demo = DemoClient(now=NOW)
    return build_server(client=demo, allow_writes=allow_writes, now=lambda: NOW), demo


def call(server, name, args=None):
    result = asyncio.run(server.call_tool(name, args or {}))
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def tool_names(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_read_only_by_default():
    server, _ = make()
    assert tool_names(server) == {"morning_brief", "triage_queue", "get_ticket", "site_health", "open_alerts"}
    assert all(t.annotations.read_only_hint for t in asyncio.run(server.list_tools()))


def test_write_tool_only_when_enabled():
    server, demo = make(allow_writes=True)
    assert "set_ticket_status" in tool_names(server)
    assert call(server, "set_ticket_status", {"ticket_id": 2045, "status": "Pending"})["updated"]
    assert demo.updates == [(2045, {"TicketStatus": "Pending"})]


def test_write_tool_rejects_unknown_status():
    server, demo = make(allow_writes=True)
    with pytest.raises(ToolError, match="must be one of"):
        asyncio.run(server.call_tool("set_ticket_status", {"ticket_id": 2045, "status": "Deleted"}))
    assert demo.updates == []


def test_morning_brief():
    b = call(make()[0], "morning_brief")
    assert b["demo_mode"] is True
    assert b["tickets"]["top"][0]["title"].startswith("<ticket_text>")


def test_triage_queue_site_filter():
    q = call(make()[0], "triage_queue", {"site": "harbor"})
    assert q["tickets"] and all(t["site"].startswith("Harbor Point") for t in q["tickets"])


def test_get_ticket_redacts_and_fences():
    t = call(make()[0], "get_ticket", {"ticket_id": 2043})
    text = json.dumps(t)
    assert "631-555-0142" not in text and "demoauto.test" not in text and "Shafer" not in text
    assert t["requester"] == "Dana S."
    assert t["comments"][0]["from"] == "Dana S."
    assert t["comments"][0]["text"].startswith("<ticket_text>")
    assert "IGNORE PREVIOUS INSTRUCTIONS" in t["comments"][0]["text"]  # kept, but fenced as data
    assert t["urgency"]["level"] == "critical"


def test_site_health_and_alerts():
    server = make()[0]
    assert call(server, "site_health")["sites"][0]["site"] == "Maplewood Kia"
    alerts = call(server, "open_alerts", {"severity": "Critical"})
    assert [a["alert_id"] for a in alerts["alerts"]] == [9001]


def test_unknown_ticket_is_a_readable_error():
    with pytest.raises(ToolError, match="No ticket #1"):
        asyncio.run(make()[0].call_tool("get_ticket", {"ticket_id": 1}))


def test_get_ticket_falls_back_when_comments_unavailable():
    from atera_triage.client import AteraError
    demo = DemoClient(now=NOW)
    original = demo.ticket
    demo.ticket = lambda tid: original(tid) | {"FirstComment": "Call me at 631-555-0142"}
    def no_comments(tid):
        raise AteraError("Atera returned HTTP 404")
    demo.ticket_comments = no_comments
    t = call(build_server(client=demo, now=lambda: NOW), "get_ticket", {"ticket_id": 2043})
    assert t["comments"][0]["text"] == "<ticket_text>Call me at [phone removed]</ticket_text>"
