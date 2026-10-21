#!/usr/bin/env python3
"""
Hourly usage report for fireatlas.org — who is visiting, minus SCS and scanners.

Reads /var/log/nginx/usage.log* (the `usage` log_format in
infrastructure/nginx/fireatlas.org: combined + vhost + owner cookie), drops

  - SCS's own traffic: lines carrying the fa_owner cookie (set by visiting
    https://fireatlas.org/identify_as_scs once per browser/phone), and any
    address listed in /etc/fireatlas/usage_exclude_ips;
  - scanners: every line from any address that requested an exploit-probe path
    (/.env, wp-*, *.php, ...) anywhere in the retained logs;

then runs GoAccess over what's left into /var/www/usage/report.html, and writes
/var/www/usage/index.html with QR-code visits (any URL with ?src=<tag>) and the
filter counts. Served at https://fireatlas.org/usage/ behind htpasswd.

Run from cron (infrastructure/cron/fireatlas-usage). Stdlib only.
"""

import argparse
import glob
import gzip
import html
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

LOG_GLOB = "/var/log/nginx/usage.log*"
OUT_DIR = "/var/www/usage"
EXCLUDE_FILE = "/etc/fireatlas/usage_exclude_ips"
ALWAYS_EXCLUDE = {"127.0.0.1", "::1", "54.241.152.98"}  # the box itself

# combined + ` $host "$cookie_fa_owner"`
LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<request>[^"]*)" '
    r'(?P<status>\d{3}) \S+ "(?P<referer>[^"]*)" "(?P<agent>[^"]*)" '
    r'(?P<host>\S+) "(?P<owner>[^"]*)"$'
)

# Paths no real visitor asks for. One hit marks the address as a scanner.
PROBE_RE = re.compile(
    r"/\.(env|git|aws|ssh|svn|DS_Store|vscode)"
    r"|wp-(admin|content|includes|login|config)|xmlrpc"
    r"|\.(php\d?|asp|aspx|jsp|cgi|sql|bak|ini)(\?|$)"
    r"|phpmyadmin|/cgi-bin/|/vendor/|/actuator|/boaform|/HNAP1|/owa/|/solr/"
    r"|/etc/passwd|/server-status|/manager/html|/\.well-known/security",
    re.IGNORECASE,
)

GOACCESS_FORMAT = [
    '--log-format=%h %^[%d:%t %^] "%r" %s %b "%R" "%u" %v "%^"',
    "--date-format=%d/%b/%Y",
    "--time-format=%T",
]


def parse(line):
    """Return the named fields of a usage-format line, or None."""
    m = LINE_RE.match(line.rstrip("\n"))
    return m.groupdict() if m else None


def request_path(request):
    """'GET /a?b=1 HTTP/1.1' → '/a?b=1' ('' for malformed requests)."""
    parts = request.split(" ")
    return parts[1] if len(parts) >= 2 else ""


def is_probe(request):
    return bool(PROBE_RE.search(request_path(request)))


def is_owner(fields, exclude_ips):
    return fields["owner"] == "1" or fields["ip"] in exclude_ips


def qr_tag(request):
    """The ?src= tag of a request, or None."""
    query = urlsplit(request_path(request)).query
    values = parse_qs(query).get("src")
    return values[0][:80] if values else None


def read_lines(paths):
    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", errors="replace") as f:
            yield from f


def load_exclude_ips(path):
    ips = set(ALWAYS_EXCLUDE)
    if os.path.exists(path):
        with open(path) as f:
            ips |= {ln.split("#")[0].strip() for ln in f} - {""}
    return ips


def scanner_ips(paths):
    """First pass: every address that probed for an exploit path."""
    found = set()
    for line in read_lines(paths):
        fields = parse(line)
        if fields and is_probe(fields["request"]):
            found.add(fields["ip"])
    return found


def filter_lines(paths, exclude_ips, scanners, counts, qr):
    """Second pass: yield the lines to report on; tally drops and QR tags."""
    for line in read_lines(paths):
        fields = parse(line)
        if not fields:
            counts["unparsed"] += 1
        elif is_owner(fields, exclude_ips):
            counts["scs"] += 1
        elif fields["ip"] in scanners:
            counts["scanner"] += 1
        else:
            counts["kept"] += 1
            tag = qr_tag(fields["request"])
            if tag:
                day = fields["time"].split(":")[0]
                qr[tag]["hits"] += 1
                qr[tag]["visitors"].add((fields["ip"], day))
                qr[tag]["hosts"].add(fields["host"])
                qr[tag]["last"] = max(qr[tag]["last"], _sortable(fields["time"]))
            yield line


def _sortable(nginx_time):
    return datetime.strptime(nginx_time, "%d/%b/%Y:%H:%M:%S %z").strftime("%Y-%m-%d %H:%M")


def write_index(out_dir, counts, scanners, qr, paths):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = "".join(
        f"<tr><td>{html.escape(tag)}</td><td>{len(d['visitors'])}</td><td>{d['hits']}</td>"
        f"<td>{html.escape(', '.join(sorted(d['hosts'])))}</td><td>{d['last']}</td></tr>"
        for tag, d in sorted(qr.items(), key=lambda kv: kv[1]["last"], reverse=True)
    ) or '<tr><td colspan="5"><em>No ?src= visits yet.</em></td></tr>'
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Fire Atlas usage</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 1.5rem; max-width: 52rem; color: #222; }}
 table {{ border-collapse: collapse; width: 100%; margin: .5rem 0 1.5rem; }}
 th, td {{ text-align: left; padding: .3rem .6rem; border-bottom: 1px solid #ddd; }}
 th {{ font-size: .8rem; text-transform: uppercase; color: #666; }}
 .big {{ font-size: 1.1rem; }} .muted {{ color: #777; font-size: .85rem; }}
</style></head><body>
<h1>Fire Atlas usage</h1>
<p class="big"><a href="report.html">Full visitor report (GoAccess) &rarr;</a></p>
<h2>QR-code visits</h2>
<p class="muted">Any URL with <code>?src=&lt;tag&gt;</code>, e.g. <code>https://fireatlas.org/?src=fair2026</code>.
Visitors = distinct address per day.</p>
<table><tr><th>Tag</th><th>Visitors</th><th>Requests</th><th>Site</th><th>Last seen (UTC)</th></tr>{rows}</table>
<h2>What was filtered out</h2>
<table>
<tr><td>Kept (real visitors)</td><td>{counts['kept']}</td></tr>
<tr><td>SCS (cookie or excluded address)</td><td>{counts['scs']}</td></tr>
<tr><td>Scanners ({len(scanners)} addresses)</td><td>{counts['scanner']}</td></tr>
<tr><td>Unparsed lines</td><td>{counts['unparsed']}</td></tr>
</table>
<p class="muted">Updated {now} from {len(paths)} log file(s).
Mark a browser as SCS: <a href="/identify_as_scs">/identify_as_scs</a>.</p>
</body></html>
"""
    tmp = os.path.join(out_dir, ".index-new.html")
    with open(tmp, "w") as f:
        f.write(page)
    os.replace(tmp, os.path.join(out_dir, "index.html"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default=LOG_GLOB)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--exclude-file", default=EXCLUDE_FILE)
    args = ap.parse_args()

    paths = sorted(glob.glob(args.logs))
    if not paths:
        sys.exit(f"No logs match {args.logs}")
    os.makedirs(args.out, exist_ok=True)

    exclude_ips = load_exclude_ips(args.exclude_file)
    scanners = scanner_ips(paths)
    counts = defaultdict(int)
    qr = defaultdict(lambda: {"hits": 0, "visitors": set(), "hosts": set(), "last": ""})

    tmp_report = os.path.join(args.out, ".report-new.html")
    goaccess = subprocess.Popen(
        ["goaccess", "-", "-o", tmp_report, "--ignore-crawlers",
         "--html-report-title=Fire Atlas visitors (SCS and scanners removed)",
         *GOACCESS_FORMAT],
        stdin=subprocess.PIPE, text=True, stdout=subprocess.DEVNULL,
    )
    for line in filter_lines(paths, exclude_ips, scanners, counts, qr):
        goaccess.stdin.write(line)
    goaccess.stdin.close()
    if goaccess.wait() != 0:
        sys.exit(f"goaccess exited {goaccess.returncode}")
    os.replace(tmp_report, os.path.join(args.out, "report.html"))

    write_index(args.out, counts, scanners, qr, paths)
    print(f"{datetime.now():%F %T} kept={counts['kept']} scs={counts['scs']} "
          f"scanner={counts['scanner']} unparsed={counts['unparsed']} qr_tags={len(qr)}")


if __name__ == "__main__":
    main()
