#!/usr/bin/env python3
"""
Trace an email photo submission through the pipeline.

Shows the status of each stage:
  1. SES receipt rule config (health check only — per-email SES logs not available without SNS, see issue #17)
  2. S3 ingress bucket — is the raw email present or was it deleted?
  3. Lambda invocation log — what happened, what did the webapp return?
  4. Feature in the layer — did a point land in the atlas?

Usage:
    python scripts/trace_email.py                        # most recent 3 Lambda invocations
    python scripts/trace_email.py <s3-object-key>        # trace a specific email
    python scripts/trace_email.py --profile atlas        # use a specific AWS profile
    python scripts/trace_email.py --atlas scvfd          # atlas name (default: scvfd)
    python scripts/trace_email.py --region us-east-1     # AWS region (default: us-east-1)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# ── defaults ─────────────────────────────────────────────────────────────────

INGRESS_BUCKET = "atlas-ingress"
INGRESS_PREFIX = "incoming/"
LAMBDA_NAME = "atlas-email-photo-handler"
LOG_GROUP = f"/aws/lambda/{LAMBDA_NAME}"
RULE_SET_NAME = "atlas-email-inlet"
DEFAULT_ATLAS = "scvfd"
DEFAULT_REGION = "us-east-1"
DEFAULT_N = 3

# ── helpers ───────────────────────────────────────────────────────────────────

def ts(ms):
    """Convert epoch milliseconds to a readable UTC string."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def ok(msg):
    print(f"  ✓  {msg}")


def warn(msg):
    print(f"  ⚠  {msg}")


def err(msg):
    print(f"  ✗  {msg}")


def info(msg):
    print(f"     {msg}")


# ── stage 1: SES health check ─────────────────────────────────────────────────

def check_ses(ses):
    section("Stage 1 — SES receipt rule config")
    try:
        resp = ses.describe_receipt_rule_set(RuleSetName=RULE_SET_NAME)
    except ClientError as e:
        err(f"Could not load rule set '{RULE_SET_NAME}': {e}")
        return

    # Check the rule set is the active one
    try:
        active = ses.describe_active_receipt_rule_set()
        active_name = active.get("Metadata", {}).get("Name")
        if active_name == RULE_SET_NAME:
            ok(f"Rule set '{RULE_SET_NAME}' is ACTIVE")
        else:
            warn(f"Rule set '{RULE_SET_NAME}' exists but is NOT active (active: {active_name!r})")
    except ClientError:
        warn("Could not check active rule set")

    rules = resp.get("Rules", [])
    if not rules:
        warn("No receipt rules found in rule set")
        return

    for rule in rules:
        name = rule.get("Name")
        enabled = rule.get("Enabled", False)
        recipients = rule.get("Recipients", [])
        actions = [a for a in rule.get("Actions", [])]
        action_types = [list(a.keys())[0] for a in actions]
        status = "enabled" if enabled else "DISABLED"
        ok(f"Rule '{name}' ({status}): recipients={recipients}, actions={action_types}")

    info("Note: per-email SES receipt logs require SNS — see GitHub issue #17")


# ── stage 2: S3 ingress bucket ────────────────────────────────────────────────

def check_s3_for_key(s3, key):
    """Check whether a specific object is present in the ingress bucket."""
    full_key = key if key.startswith(INGRESS_PREFIX) else INGRESS_PREFIX + key
    try:
        resp = s3.head_object(Bucket=INGRESS_BUCKET, Key=full_key)
        size = resp["ContentLength"]
        modified = resp["LastModified"].strftime("%Y-%m-%d %H:%M:%S UTC")
        ok(f"Raw email PRESENT in S3: {full_key}")
        info(f"Size: {size:,} bytes, last modified: {modified}")
        info("(Still in bucket — not yet successfully processed)")
        return full_key
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            ok(f"Raw email NOT in bucket (deleted after successful processing): {full_key}")
        else:
            err(f"S3 error checking {full_key}: {e}")
        return full_key


def list_recent_s3_objects(s3, n):
    """List the n most recent objects in the ingress prefix (n=0 returns all)."""
    try:
        resp = s3.list_objects_v2(Bucket=INGRESS_BUCKET, Prefix=INGRESS_PREFIX)
        objects = resp.get("Contents", [])
        objects.sort(key=lambda o: o["LastModified"], reverse=True)
        return objects if n == 0 else objects[:n]
    except ClientError as e:
        err(f"Could not list ingress bucket: {e}")
        return []


# ── stage 3: Lambda CloudWatch logs ───────────────────────────────────────────

def get_recent_streams(logs, n):
    """Return the n most recently active log streams (n=0 returns all via pagination)."""
    try:
        streams = []
        kwargs = dict(logGroupName=LOG_GROUP, orderBy="LastEventTime", descending=True, limit=50)
        while True:
            resp = logs.describe_log_streams(**kwargs)
            streams.extend(resp.get("logStreams", []))
            token = resp.get("nextToken")
            if not token or (n > 0 and len(streams) >= n):
                break
            kwargs["nextToken"] = token
        return streams if n == 0 else streams[:n]
    except ClientError as e:
        err(f"Could not list log streams: {e}")
        return []


def parse_invocations_from_stream(logs, stream_name):
    """
    Extract individual Lambda invocations from a stream.
    Returns a list of dicts with keys: request_id, start_ms, lines, s3_key, status, webapp_response.
    """
    try:
        resp = logs.get_log_events(
            logGroupName=LOG_GROUP,
            logStreamName=stream_name,
            startFromHead=True,
        )
    except ClientError as e:
        err(f"Could not read stream {stream_name}: {e}")
        return []

    events = resp.get("events", [])
    invocations = []
    current = None

    for event in events:
        msg = event["message"].rstrip()
        ts_ms = event["timestamp"]

        if msg.startswith("START RequestId:"):
            rid = msg.split()[2]
            current = {"request_id": rid, "start_ms": ts_ms, "lines": [], "s3_key": None,
                       "status": "in-progress", "webapp_response": None, "end_ms": None}
            invocations.append(current)

        if current is None:
            continue

        current["lines"].append(msg)

        # Extract S3 key being processed
        m = re.search(r"Processing s3://[^/]+/(\S+)", msg)
        if m:
            current["s3_key"] = m.group(1)

        # Extract webapp response
        m = re.search(r"Webapp response: (\d+) (.+)", msg)
        if m:
            current["webapp_response"] = (int(m.group(1)), m.group(2))

        if msg.startswith("END RequestId:"):
            current["end_ms"] = ts_ms
            if current["webapp_response"]:
                code = current["webapp_response"][0]
                current["status"] = "success" if code == 200 else "webapp-error"
            else:
                current["status"] = "lambda-error"

    return invocations


def get_invocations_for_key(logs, streams, s3_key):
    """Search all streams for an invocation that processed a given S3 key."""
    for stream in streams:
        invocations = parse_invocations_from_stream(logs, stream["logStreamName"])
        for inv in invocations:
            if inv["s3_key"] and s3_key in inv["s3_key"]:
                return [inv]
    return []


def print_invocation(inv):
    rid = inv["request_id"]
    start = ts(inv["start_ms"]) if inv["start_ms"] else "?"
    duration = f"{(inv['end_ms'] - inv['start_ms']) / 1000:.1f}s" if inv["end_ms"] and inv["start_ms"] else "?"

    status = inv["status"]
    if status == "success":
        ok(f"Invocation {rid[:8]}… — SUCCESS ({start}, {duration})")
    elif status == "in-progress":
        warn(f"Invocation {rid[:8]}… — IN PROGRESS ({start})")
    else:
        err(f"Invocation {rid[:8]}… — FAILED ({start}, {duration})")

    if inv["s3_key"]:
        info(f"S3 key: {inv['s3_key']}")

    if inv["webapp_response"]:
        code, body = inv["webapp_response"]
        info(f"Webapp: HTTP {code}  {body}")

    # For failures, dump all non-boilerplate lines; for success, only print ERROR lines
    boilerplate = ("START RequestId:", "END RequestId:", "REPORT RequestId:")
    if inv["status"] != "success":
        for line in inv["lines"]:
            if not any(line.startswith(p) for p in boilerplate):
                info(f"  {line}")
    else:
        for line in inv["lines"]:
            if "[ERROR]" in line or ("Error" in line and "Webapp" not in line):
                err(f"  {line.split(']', 2)[-1].strip()}")


# ── stage 4: feature in layer ─────────────────────────────────────────────────

def check_feature(inv, atlas_name, data_root):
    """
    Try to find the feature that was created by this invocation.
    Looks at the delta files for the layer mentioned in the webapp response.
    """
    if not inv.get("webapp_response"):
        return
    code, body = inv["webapp_response"]
    if code != 200:
        return

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return

    layer = data.get("layer")
    lat = data.get("lat")
    lon = data.get("lon")

    if not layer:
        return

    ok(f"Feature created in layer '{layer}' at ({lat}, {lon})")

    # Try to find the delta file on disk (only works if running on the server)
    if data_root:
        delta_dir = os.path.join(data_root, atlas_name, "staging", "deltas", layer)
        if os.path.isdir(delta_dir):
            deltas = sorted(os.listdir(delta_dir), reverse=True)
            if deltas:
                info(f"Most recent delta: {deltas[0]}")
            else:
                warn(f"Delta directory exists but is empty: {delta_dir}")
        else:
            info(f"(Delta dir not found locally — run on server to verify: {delta_dir})")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trace an email photo submission through the pipeline.")
    parser.add_argument("s3_key", nargs="?", help="S3 object key to trace (optional)")
    parser.add_argument("--profile", default="atlas", help="AWS profile (default: atlas)")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument("--atlas", default=DEFAULT_ATLAS, help=f"Atlas name (default: {DEFAULT_ATLAS})")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"Number of recent invocations to show (default: {DEFAULT_N}; 0 = all)")
    parser.add_argument("--failed", action="store_true", help="Only show invocations that did not complete successfully")
    parser.add_argument("--ignore-key", metavar="SUBSTRING", help="Exclude invocations whose S3 key contains this substring")
    parser.add_argument("--data-root", default=os.environ.get("DATASWALE_PATH"), help="Path to atlas data root (for feature verification)")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    ses = session.client("ses")
    s3 = session.client("s3")
    logs = session.client("logs")

    print(f"\nEmail Photo Inlet — Pipeline Trace")
    print(f"Atlas: {args.atlas}  |  Region: {args.region}  |  Profile: {args.profile}")

    # ── Stage 1: SES ──
    check_ses(ses)

    # ── Stage 2: S3 ──
    section("Stage 2 — S3 ingress bucket")
    if args.s3_key:
        check_s3_for_key(s3, args.s3_key)
    else:
        objects = list_recent_s3_objects(s3, args.n)
        if objects:
            warn(f"{len(objects)} unprocessed email(s) currently in bucket:")
            for obj in objects:
                info(f"  {obj['Key']}  ({obj['Size']:,} bytes, {obj['LastModified'].strftime('%Y-%m-%d %H:%M:%S UTC')})")
        else:
            ok("No unprocessed emails in ingress bucket")

    # ── Stage 3: Lambda logs ──
    section("Stage 3 — Lambda invocation log")
    stream_multiplier = 10 if args.failed else 3
    streams = get_recent_streams(logs, 0 if args.n == 0 else args.n * stream_multiplier)
    if not streams:
        err("No Lambda log streams found — has the function ever run?")
        sys.exit(1)

    if args.s3_key:
        key = args.s3_key if args.s3_key.startswith(INGRESS_PREFIX) else INGRESS_PREFIX + args.s3_key
        invocations = get_invocations_for_key(logs, streams, args.s3_key)
        if not invocations:
            warn(f"No Lambda invocation found for key '{args.s3_key}' in recent log streams")
            info("The email may not have triggered Lambda yet, or logs may have expired")
        for inv in invocations:
            print_invocation(inv)
            section("Stage 4 — Feature in layer")
            check_feature(inv, args.atlas, args.data_root)
    else:
        # Collect up to N most recent invocations across streams
        all_invocations = []
        for stream in streams:
            invs = parse_invocations_from_stream(logs, stream["logStreamName"])
            all_invocations.extend(invs)

        # Sort by start time descending, optionally deduplicate by key and filter to failures, take N
        all_invocations.sort(key=lambda i: i["start_ms"] or 0, reverse=True)
        if args.ignore_key:
            all_invocations = [i for i in all_invocations if args.ignore_key not in (i["s3_key"] or "")]
        total_checked = len(all_invocations)
        if args.failed:
            # One entry per email: keep only the most recent attempt per s3_key,
            # then show only those where that most recent attempt failed.
            seen = {}
            for inv in all_invocations:
                key = inv["s3_key"] or inv["request_id"]
                if key not in seen:
                    seen[key] = inv
            all_invocations = [i for i in seen.values() if i["status"] != "success"]
        recent = all_invocations if args.n == 0 else all_invocations[:args.n]

        if not recent:
            if args.failed:
                ok(f"No failed invocations found in the last {total_checked} checked")
            else:
                warn("No invocations found in recent log streams")
        else:
            if args.failed:
                info(f"Showing {len(recent)} failed invocation(s) (of {total_checked} total checked):\n")
            else:
                info(f"Showing {len(recent)} most recent invocation(s):\n")
            for inv in recent:
                print_invocation(inv)
                if not args.failed:
                    section("Stage 4 — Feature in layer")
                    check_feature(inv, args.atlas, args.data_root)

    print()


if __name__ == "__main__":
    main()
