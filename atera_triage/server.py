"""The MCP server: triage tools for an IT help desk running on Atera.

Configuration (environment variables):
  ATERA_API_KEY       Your Atera API key. Without it the server runs in demo mode.
  ATERA_ALLOW_WRITES  Set to "true" to enable the one write tool (set_ticket_status).
"""

from __future__ import annotations

import datetime as dt
import functools
import os
from dataclasses import asdict
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, triage
from .client import AteraClient, AteraError
from .demo import DemoClient
from .privacy import UNTRUSTED_NOTE, clean_record, fence, short_name

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
TICKET_STATUSES = ("Open", "Pending", "Resolved", "Closed")

INSTRUCTIONS = f"""Help an IT manager triage their Atera help desk.
Start with morning_brief or triage_queue, then drill into get_ticket, site_health or open_alerts.
Personal contact details are removed before you see them; don't ask for them.
{UNTRUSTED_NOTE}
Never change a ticket unless the user asked for that specific change."""


def _friendly(fn):
    """Pass expected failures (bad key, unknown ticket, bad input) to the model as readable errors."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (AteraError, LookupError, ValueError) as e:
            raise ToolError(str(e)) from e
    return wrapper


def build_server(client: Any = None, allow_writes: bool | None = None, now=None) -> MCPServer:
    """Create the server. Arguments exist so tests can inject a client and a fixed clock."""
    if client is None:
        key = os.environ.get("ATERA_API_KEY", "").strip()
        client = AteraClient(key) if key else DemoClient()
    if allow_writes is None:
        allow_writes = os.environ.get("ATERA_ALLOW_WRITES", "").lower() in ("1", "true", "yes")
    clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
    demo = isinstance(client, DemoClient)

    server = MCPServer(
        "atera-triage",
        title="Atera Help Desk Triage",
        instructions=INSTRUCTIONS + ("\nDEMO MODE: all data is fictional sample data." if demo else ""),
        version=__version__,
    )

    @server.tool(annotations=READ_ONLY)
    @_friendly
    def morning_brief() -> dict:
        """One-call summary: open and critical tickets, the top three to handle first,
        offline devices and the site with the most, and active alerts."""
        result = triage.brief(client.tickets(), client.agents(), client.alerts(), clock())
        for item in result["tickets"]["top"]:
            item["title"] = fence(item["title"])
        result["demo_mode"] = demo
        result["note"] = UNTRUSTED_NOTE
        return result

    @server.tool(annotations=READ_ONLY)
    @_friendly
    def triage_queue(limit: int = 15, site: str | None = None) -> dict:
        """Open tickets ranked by urgency, with the reasons for each ranking.
        Levels: critical (e.g. waiting on IT over 24 h or SLA missed), high, normal,
        and waiting_on_user (IT replied last). Optionally filter by site name."""
        ranked = triage.rank_queue(client.tickets(), clock())
        if site:
            ranked = [r for r in ranked if site.lower() in r.site.lower()]
        rows = [asdict(r) | {"title": fence(r.title)} for r in ranked[: max(1, min(limit, 100))]]
        return {"count": len(ranked), "tickets": rows, "note": UNTRUSTED_NOTE}

    @server.tool(annotations=READ_ONLY)
    @_friendly
    def get_ticket(ticket_id: int) -> dict:
        """Full details and conversation for one ticket, with contact details removed
        and user-written text fenced as untrusted."""
        t = clean_record(client.ticket(ticket_id))
        comments = []
        try:
            raw_comments = client.ticket_comments(ticket_id)
        except AteraError:
            # Fall back to the first and latest user messages Atera includes on every ticket.
            raw_comments = [{"Name": "Requester", "Comment": t[k]}
                            for k in ("FirstComment", "LastEndUserComment") if t.get(k)]
        for c in raw_comments:
            if c.get("FirstName") or c.get("LastName"):
                author = short_name(c.get("FirstName"), c.get("LastName"))
            else:
                first, _, last = str(c.get("Name") or "").partition(" ")
                author = short_name(first, last)
            comments.append({
                "from": author,
                "at": c.get("Date") or c.get("CommentTimestamp"),
                "internal": bool(c.get("IsInternal")),
                "text": fence(str(c.get("Comment") or c.get("CommentText") or "")),
            })
        ranked = triage.rank_ticket(client.ticket(ticket_id), clock())
        return {
            "ticket_id": t.get("TicketID"),
            "title": fence(str(t.get("TicketTitle", ""))),
            "site": t.get("CustomerName"),
            "status": t.get("TicketStatus"),
            "priority": t.get("TicketPriority"),
            "requester": short_name(t.get("EndUserFirstName"), t.get("EndUserLastName")),
            "technician": t.get("TechnicianFullName"),
            "created": t.get("TicketCreatedDate"),
            "urgency": {"level": ranked.level, "reasons": ranked.reasons},
            "comments": comments,
            "note": UNTRUSTED_NOTE,
        }

    @server.tool(annotations=READ_ONLY)
    @_friendly
    def site_health(site: str | None = None) -> dict:
        """Devices per site (Atera customer): how many are online, and which machines
        are offline and since when. Sites with the most offline devices come first."""
        sites = triage.site_health(client.agents(), clock())
        if site:
            sites = [s for s in sites if site.lower() in s["site"].lower()]
        return {"sites": sites}

    @server.tool(annotations=READ_ONLY)
    @_friendly
    def open_alerts(severity: str | None = None, limit: int = 25) -> dict:
        """Active (unarchived) Atera alerts, most severe and newest first.
        severity can be Critical, Warning or Information."""
        rows = triage.open_alerts(client.alerts(), severity)
        return {"count": len(rows), "alerts": rows[: max(1, min(limit, 100))]}

    if allow_writes:
        @server.tool(annotations=WRITE)
        @_friendly
        def set_ticket_status(ticket_id: int, status: str) -> dict:
            """Change a ticket's status (Open, Pending, Resolved or Closed).
            Only use this when the user explicitly asked for this exact change."""
            if status not in TICKET_STATUSES:
                raise ValueError(f"status must be one of {', '.join(TICKET_STATUSES)}")
            client.update_ticket(ticket_id, {"TicketStatus": status})
            return {"ticket_id": ticket_id, "status": status, "updated": True}

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
