"""
Atlas log queries — shared between the webapp API and outlet generation.

Currently covers the email inlet pipeline (SES → S3 → Lambda → webapp).
Add more log sources here as needed.
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

INGRESS_BUCKET = "atlas-ingress"
INGRESS_PREFIX = "incoming/"
LAMBDA_NAME = "atlas-email-photo-handler"
LOG_GROUP = f"/aws/lambda/{LAMBDA_NAME}"


def _ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_invocations(events):
    """Parse raw CloudWatch log events into invocation dicts."""
    invocations = []
    current = None

    for event in events:
        msg = event["message"].rstrip()
        ts_ms = event["timestamp"]

        if msg.startswith("START RequestId:"):
            rid = msg.split()[2]
            current = {"request_id": rid, "start_ms": ts_ms, "end_ms": None,
                       "s3_key": None, "subject": None, "sender": None,
                       "webapp_response": None, "status": "in-progress"}
            invocations.append(current)

        if current is None:
            continue

        m = re.search(r"Processing s3://[^/]+/(\S+)", msg)
        if m:
            current["s3_key"] = m.group(1)

        m = re.search(r"Subject: (.+)", msg)
        if m:
            current["subject"] = m.group(1).strip()

        m = re.search(r"From: (.+)", msg)
        if m:
            current["sender"] = m.group(1).strip()

        m = re.search(r"Webapp response: (\d+) (.+)", msg)
        if m:
            current["webapp_response"] = (int(m.group(1)), m.group(2))

        if msg.startswith("END RequestId:"):
            current["end_ms"] = ts_ms
            if current["webapp_response"]:
                code = current["webapp_response"][0]
                current["status"] = "success" if code == 200 else "error"
            else:
                current["status"] = "lambda-error"

    return invocations


def fetch_email_invocations(n=3, region="us-east-1"):
    """
    Return the n most recent email inlet Lambda invocations from CloudWatch.

    Each entry is a dict with keys:
        request_id, start_ms, end_ms, s3_key, webapp_response, status
    Returns [] if CloudWatch is unreachable or permissions are missing.
    """
    try:
        import boto3
        logs = boto3.client("logs", region_name=region)

        streams = logs.describe_log_streams(
            logGroupName=LOG_GROUP,
            orderBy="LastEventTime",
            descending=True,
            limit=n * 3,
        ).get("logStreams", [])

        all_invocations = []
        for stream in streams:
            events = logs.get_log_events(
                logGroupName=LOG_GROUP,
                logStreamName=stream["logStreamName"],
                startFromHead=True,
            ).get("events", [])
            all_invocations.extend(_parse_invocations(events))

        all_invocations.sort(key=lambda i: i["start_ms"] or 0, reverse=True)
        return all_invocations[:n]

    except Exception as e:
        logger.warning(f"Could not fetch email log from CloudWatch: {e}")
        return []


def format_as_use_cases(invocations):
    """
    Format invocations as use_cases entries for the console template.
    Each invocation becomes a text-only entry (cases=[]).
    """
    if not invocations:
        return [{"name": "Email inlet: no log data available (check CloudWatch permissions)", "cases": []}]

    import json as _json
    entries = []
    for inv in invocations:
        icon = "✓" if inv["status"] == "success" else "✗"
        start = _ts(inv["start_ms"]) if inv["start_ms"] else "?"
        duration = (
            f"{(inv['end_ms'] - inv['start_ms']) / 1000:.1f}s"
            if inv["end_ms"] and inv["start_ms"] else "?"
        )

        subject = inv.get("subject") or "?"
        sender = inv.get("sender") or "?"
        # Shorten "Display Name <email>" to just the email
        if "<" in sender:
            sender = sender.split("<")[1].rstrip(">")

        image_url = None
        if inv["webapp_response"]:
            code, body = inv["webapp_response"]
            try:
                parsed = _json.loads(body)
                detail = parsed.get("detail", body)
                image_url = parsed.get("image_url")
                content_type = parsed.get("content_type")
            except Exception:
                detail = body[:80]
                content_type = None
            ct_str = f" [{content_type}]" if content_type else ""
            detail_str = f"HTTP {code}{ct_str} — {detail}"
        else:
            detail_str = "Lambda error (no webapp response)"

        cases = []
        if image_url:
            cases.append({"name": "View photo", "uri": image_url})

        entries.append({
            "name": f"{icon} {start} ({duration}) — {sender} — {subject} — {detail_str}",
            "cases": cases
        })

    return entries
