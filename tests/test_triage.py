import datetime as dt

from atera_triage import triage
from atera_triage.demo import DemoClient

NOW = dt.datetime(2026, 10, 19, 13, 0, tzinfo=dt.timezone.utc)


def iso(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket(**kw):
    base = {"TicketID": 1, "TicketTitle": "Test", "TicketStatus": "Open", "TicketPriority": "Medium",
            "CustomerName": "Store", "TicketCreatedDate": iso(2)}
    return base | kw


def test_parse_time_handles_zone_and_garbage():
    assert triage.parse_time("2026-10-19T12:00:00Z").tzinfo is not None
    assert triage.parse_time("2026-10-19T12:00:00").tzinfo is not None
    assert triage.parse_time("not a date") is None
    assert triage.parse_time(None) is None


def test_no_reply_from_it_waits_on_us():
    r = triage.rank_ticket(ticket(), NOW)
    assert r.level == "normal"
    assert r.reasons == ["no reply from IT yet (2 h old)"]


def test_over_24h_without_reply_is_critical():
    r = triage.rank_ticket(ticket(TicketCreatedDate=iso(30)), NOW)
    assert r.level == "critical"
    assert r.reasons[0] == "waiting on IT over 24 h"


def test_missed_first_response_sla():
    r = triage.rank_ticket(ticket(FirstResponseDueDate=iso(1)), NOW)
    assert "first-response SLA missed" in r.reasons
    assert r.level == "high"


def test_user_replied_after_tech():
    r = triage.rank_ticket(ticket(LastTechnicianCommentTimestamp=iso(5), LastEndUserCommentTimestamp=iso(1)), NOW)
    assert r.reasons == ["user replied 1 h ago"]


def test_tech_replied_last_is_waiting_on_user():
    r = triage.rank_ticket(ticket(TicketPriority="High", TicketCreatedDate=iso(40),
                                  LastTechnicianCommentTimestamp=iso(1), LastEndUserCommentTimestamp=iso(3)), NOW)
    assert r.level == "waiting_on_user"


def test_queue_skips_closed_and_folds_duplicates():
    tickets = [
        ticket(TicketID=1, TicketTitle="New hire: Sam", EndUserID="hr"),
        ticket(TicketID=2, TicketTitle="new hire: sam ", EndUserID="hr"),
        ticket(TicketID=3, TicketTitle="New hire: Sam", EndUserID="someone.else"),
        ticket(TicketID=4, TicketStatus="Resolved"),
    ]
    assert [r.ticket_id for r in triage.rank_queue(tickets, NOW)] == [1, 3]


def test_queue_orders_by_level_then_score():
    tickets = [
        ticket(TicketID=1, LastTechnicianCommentTimestamp=iso(1)),           # waiting on user
        ticket(TicketID=2, TicketCreatedDate=iso(30)),                       # critical
        ticket(TicketID=3),                                                  # normal
    ]
    assert [r.ticket_id for r in triage.rank_queue(tickets, NOW)] == [2, 3, 1]


def test_site_health_counts_and_sorts():
    agents = [
        {"MachineName": "A-1", "CustomerName": "A", "Online": True},
        {"MachineName": "B-1", "CustomerName": "B", "Online": False, "LastSeen": iso(2)},
        {"MachineName": "B-2", "CustomerName": "B", "Online": False, "LastSeen": iso(30)},
        {"MachineName": "B-3", "CustomerName": "B", "Online": False},
    ]
    sites = triage.site_health(agents, NOW)
    assert sites[0]["site"] == "B" and sites[0]["offline_count"] == 3
    assert [m["machine"] for m in sites[0]["offline"]] == ["B-3", "B-2", "B-1"]
    assert sites[0]["offline"][0]["last_seen"] == "unknown"
    assert sites[1] == {"site": "A", "devices": 1, "online": 1, "offline": [], "offline_count": 0}


def test_open_alerts_drops_archived_and_sorts():
    alerts = [
        {"AlertID": 1, "Severity": "Warning", "Created": iso(1)},
        {"AlertID": 2, "Severity": "Critical", "Created": iso(5)},
        {"AlertID": 3, "Severity": "Warning", "Created": iso(3)},
        {"AlertID": 4, "Severity": "Critical", "Created": iso(1), "Archived": True},
    ]
    assert [a["alert_id"] for a in triage.open_alerts(alerts)] == [2, 1, 3]
    assert [a["alert_id"] for a in triage.open_alerts(alerts, "warning")] == [1, 3]


def test_brief_on_demo_data():
    demo = DemoClient(now=NOW)
    b = triage.brief(demo.tickets(), demo.agents(), demo.alerts(), NOW)
    assert b["tickets"]["open"] == 7  # 9 tickets, minus one resolved and one duplicate
    assert b["tickets"]["top"][0]["ticket_id"] == 2043
    assert b["devices"] == {"total": 302, "offline": 5, "worst_site": "Maplewood Kia"}
    assert b["alerts"] == {"critical": 1, "warning": 2}
