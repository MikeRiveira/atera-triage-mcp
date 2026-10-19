"""Triage rules: turn raw Atera data into "what needs me first".

Everything here is a pure function of the data and the current time, so the
rules are easy to test and to reason about.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

OPEN_STATUSES = {"Open", "Pending"}
PRIORITY_WEIGHT = {"Critical": 30, "High": 20, "Medium": 10, "Low": 0}
SEVERITY_ORDER = {"Critical": 0, "Warning": 1, "Information": 2}


def parse_time(value: Any) -> dt.datetime | None:
    """Parse Atera's ISO timestamps (with or without a zone; Atera uses UTC)."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def hours_since(value: Any, now: dt.datetime) -> float | None:
    moment = parse_time(value)
    return None if moment is None else (now - moment).total_seconds() / 3600


def _ago(hours: float) -> str:
    if hours < 1:
        return f"{max(1, round(hours * 60))} min"
    if hours < 48:
        return f"{round(hours)} h"
    return f"{round(hours / 24)} days"


@dataclass
class RankedTicket:
    ticket_id: int
    title: str
    site: str
    status: str
    priority: str
    level: str          # critical | high | normal | waiting_on_user
    score: int
    reasons: list[str] = field(default_factory=list)


def rank_ticket(t: dict[str, Any], now: dt.datetime) -> RankedTicket:
    """Score one open ticket. Higher score = handle sooner."""
    priority = t.get("TicketPriority") or "Medium"
    score = PRIORITY_WEIGHT.get(priority, 10)
    reasons: list[str] = []

    created_h = hours_since(t.get("TicketCreatedDate"), now) or 0
    user_h = hours_since(t.get("LastEndUserCommentTimestamp"), now)
    tech_h = hours_since(t.get("LastTechnicianCommentTimestamp"), now)

    if tech_h is None:
        # Nobody from IT has replied yet.
        waiting_h = created_h
        reasons.append(f"no reply from IT yet ({_ago(created_h)} old)")
        waiting_on_us = True
    elif user_h is not None and user_h < tech_h:
        # The user wrote after our last reply.
        waiting_h = user_h
        reasons.append(f"user replied {_ago(user_h)} ago")
        waiting_on_us = True
    else:
        waiting_h = 0.0
        waiting_on_us = False

    if waiting_on_us:
        score += 15 + min(int(waiting_h), 72)
        if waiting_h >= 24:
            score += 40
            reasons.insert(0, "waiting on IT over 24 h")

    due = parse_time(t.get("FirstResponseDueDate"))
    if due and tech_h is None and now > due:
        score += 30
        reasons.insert(0, "first-response SLA missed")

    if priority in ("Critical", "High"):
        reasons.append(f"{priority.lower()} priority")

    if not waiting_on_us:
        level = "waiting_on_user"
    elif score >= 70:
        level = "critical"
    elif score >= 40:
        level = "high"
    else:
        level = "normal"

    return RankedTicket(
        ticket_id=int(t.get("TicketID", 0)),
        title=str(t.get("TicketTitle", "")),
        site=str(t.get("CustomerName") or "Unassigned"),
        status=str(t.get("TicketStatus", "")),
        priority=priority,
        level=level,
        score=score,
        reasons=reasons,
    )


def rank_queue(tickets: list[dict[str, Any]], now: dt.datetime) -> list[RankedTicket]:
    """Rank every open ticket, most urgent first. Duplicate titles from the same
    user (a common HR form double-submit) are folded into the first one."""
    seen: set[tuple[str, str]] = set()
    ranked = []
    for t in tickets:
        if t.get("TicketStatus") not in OPEN_STATUSES:
            continue
        key = (str(t.get("TicketTitle", "")).strip().lower(), str(t.get("EndUserID") or t.get("EndUserEmail") or ""))
        if key in seen and key[1]:
            continue
        seen.add(key)
        ranked.append(rank_ticket(t, now))
    order = {"critical": 0, "high": 1, "normal": 2, "waiting_on_user": 3}
    return sorted(ranked, key=lambda r: (order[r.level], -r.score, r.ticket_id))


def site_health(agents: list[dict[str, Any]], now: dt.datetime) -> list[dict[str, Any]]:
    """Group devices by site (Atera customer) with online counts and offline machines."""
    sites: dict[str, dict[str, Any]] = {}
    for a in agents:
        name = str(a.get("CustomerName") or "Unassigned")
        site = sites.setdefault(name, {"site": name, "devices": 0, "online": 0, "offline": []})
        site["devices"] += 1
        if a.get("Online"):
            site["online"] += 1
        else:
            last = hours_since(a.get("LastSeen"), now)
            site["offline"].append({
                "machine": a.get("MachineName", "?"),
                "last_seen": f"{_ago(last)} ago" if last is not None else "unknown",
                "_hours": last if last is not None else 1e9,
            })
    result = []
    for site in sites.values():
        site["offline"].sort(key=lambda m: -m["_hours"])  # longest offline first
        for machine in site["offline"]:
            del machine["_hours"]
        site["offline_count"] = len(site["offline"])
        result.append(site)
    return sorted(result, key=lambda s: (-s["offline_count"], s["site"]))


def open_alerts(alerts: list[dict[str, Any]], severity: str | None = None) -> list[dict[str, Any]]:
    """Unarchived alerts, most severe and newest first."""
    rows = [a for a in alerts if not a.get("Archived")]
    if severity:
        rows = [a for a in rows if str(a.get("Severity", "")).lower() == severity.lower()]
    rows.sort(key=lambda a: str(a.get("Created", "")), reverse=True)
    rows.sort(key=lambda a: SEVERITY_ORDER.get(a.get("Severity"), 3))
    return [{
        "alert_id": a.get("AlertID"),
        "severity": a.get("Severity"),
        "title": a.get("Title"),
        "machine": a.get("DeviceName") or a.get("MachineName"),
        "site": a.get("CustomerName"),
        "created": a.get("Created"),
    } for a in rows]


def brief(tickets: list[dict[str, Any]], agents: list[dict[str, Any]],
          alerts: list[dict[str, Any]], now: dt.datetime) -> dict[str, Any]:
    """A morning summary across the queue, devices and alerts."""
    queue = rank_queue(tickets, now)
    sites = site_health(agents, now)
    active = open_alerts(alerts)
    return {
        "tickets": {
            "open": len(queue),
            "critical": sum(r.level == "critical" for r in queue),
            "waiting_on_it": sum(r.level != "waiting_on_user" for r in queue),
            "top": [{"ticket_id": r.ticket_id, "title": r.title, "site": r.site, "why": r.reasons}
                    for r in queue[:3] if r.level != "waiting_on_user"],
        },
        "devices": {
            "total": sum(s["devices"] for s in sites),
            "offline": sum(s["offline_count"] for s in sites),
            "worst_site": sites[0]["site"] if sites and sites[0]["offline_count"] else None,
        },
        "alerts": {
            "critical": sum(a["severity"] == "Critical" for a in active),
            "warning": sum(a["severity"] == "Warning" for a in active),
        },
    }
