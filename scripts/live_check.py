"""Check that this server understands your Atera account, without showing any ticket data.

Prints only HTTP results, record counts and field names, so the output is safe to share
in an issue. Needs only the Python standard library (no MCP install).

    python scripts/live_check.py --key-file atera_key.txt
    (or set ATERA_API_KEY and run without --key-file)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atera_triage import triage  # noqa: E402
from atera_triage.client import AteraClient, AteraError  # noqa: E402

# Fields the triage tools read from each kind of record.
EXPECTED = {
    "tickets": ["TicketID", "TicketTitle", "TicketStatus", "TicketPriority", "CustomerName", "EndUserID",
                "EndUserFirstName", "EndUserLastName", "TicketCreatedDate", "LastEndUserCommentTimestamp",
                "LastTechnicianCommentTimestamp", "FirstResponseDueDate", "TechnicianFullName"],
    "comments": ["Date", "Comment", "IsInternal", "FirstName", "LastName"],
    "agents": ["AgentID", "MachineName", "CustomerName", "Online", "LastSeen"],
    "alerts": ["AlertID", "Severity", "Title", "DeviceName", "CustomerName", "Created", "Archived"],
}


def report(name: str, rows: list[dict]) -> None:
    seen = sorted({k for r in rows for k in r})
    missing = [f for f in EXPECTED[name] if f not in seen]
    print(f"\n[{name}] {len(rows)} records")
    print(f"  missing expected fields: {', '.join(missing) or 'none'}")
    print(f"  all fields: {', '.join(seen)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", help="text file holding only the API key")
    args = parser.parse_args()
    key = Path(args.key_file).read_text(encoding="utf-8-sig").strip() if args.key_file else os.environ.get("ATERA_API_KEY", "").strip()
    if not key:
        print("No API key: pass --key-file or set ATERA_API_KEY")
        return 2

    client = AteraClient(key)
    now = dt.datetime.now(dt.timezone.utc)
    ok = True
    data: dict[str, list[dict]] = {}
    for name, fetch in (("tickets", client.tickets), ("agents", client.agents), ("alerts", client.alerts)):
        try:
            data[name] = fetch()
            report(name, data[name])
        except AteraError as e:
            ok = False
            data[name] = []
            print(f"\n[{name}] FAILED: {e}")

    tickets = data["tickets"]
    if tickets:
        open_ones = [t for t in tickets if t.get("TicketStatus") in triage.OPEN_STATUSES] or tickets
        tid = open_ones[0].get("TicketID")
        try:
            client.ticket(tid)
            print("\n[single ticket] OK")
        except AteraError as e:
            ok = False
            print(f"\n[single ticket] FAILED: {e}")
        try:
            report("comments", client.ticket_comments(tid))
        except AteraError as e:
            ok = False
            print(f"\n[comments] FAILED: {e}")
        print(f"\n[ticket statuses] {dict(Counter(t.get('TicketStatus') for t in tickets))}")
        print(f"[ticket priorities] {dict(Counter(t.get('TicketPriority') for t in tickets))}")

    b = triage.brief(tickets, data["agents"], data["alerts"], now)
    levels = Counter(r.level for r in triage.rank_queue(tickets, now))
    print("\n[triage summary]")
    print(f"  open tickets: {b['tickets']['open']}  levels: {dict(levels)}")
    print(f"  devices: {b['devices']['total']}  offline: {b['devices']['offline']}")
    print(f"  active alerts: critical {b['alerts']['critical']}, warning {b['alerts']['warning']}")
    print("\nRESULT:", "all calls succeeded" if ok else "some calls failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
