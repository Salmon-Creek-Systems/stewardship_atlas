#!/usr/bin/env python3
"""
Diagnostic SDA query for one or more mukeys.

Reads mukeys from stdin (one per line) and prints component + horizon
data from NRCS Soil Data Access to stdout as tab-separated values.

Usage:
    echo '2711143' | python scripts/query_sda.py
    echo -e '2711143\n2609528' | python scripts/query_sda.py
    cat mukeys.txt | python scripts/query_sda.py | column -t
    echo '2711143' | python scripts/query_sda.py --json
"""

import sys
import json
import argparse
import requests

SDA_TABULAR_URL = 'https://sdmdataaccess.nrcs.usda.gov/tabular/post.rest'


def sda_post(sql: str, timeout: int = 30) -> list:
    body = {'query': sql, 'format': 'json+columnname'}
    resp = requests.post(SDA_TABULAR_URL, json=body, timeout=timeout)
    if not resp.ok:
        raise SystemExit(f"SDA error {resp.status_code}: {resp.text[:500]}")
    return resp.json().get('Table', [])


def main():
    parser = argparse.ArgumentParser(
        description='Diagnostic SDA component/horizon query. Reads mukeys from stdin.'
    )
    parser.add_argument('--json', action='store_true', help='Output as JSON instead of TSV')
    args = parser.parse_args()

    mukeys = [line.strip() for line in sys.stdin if line.strip()]
    if not mukeys:
        raise SystemExit("No mukeys on stdin.")

    mukey_list = ', '.join(f"'{m}'" for m in mukeys)
    sql = f"""
        SELECT co.mukey, co.cokey, co.compname, co.comppct_r, co.majcompflag,
               ch.hzdept_r, ch.hzdepb_r, ch.ph1to1h2o_r, ch.om_r,
               ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r, ch.hzname, ch.desgnmaster
        FROM component co
        LEFT JOIN chorizon ch ON co.cokey = ch.cokey
        WHERE co.mukey IN ({mukey_list})
        ORDER BY co.mukey, co.comppct_r DESC, ch.hzdept_r
    """

    rows = sda_post(sql)

    if len(rows) < 2:
        print("No data returned.", file=sys.stderr)
        sys.exit(0)

    headers, data = rows[0], rows[1:]

    if args.json:
        records = [dict(zip(headers, row)) for row in data]
        json.dump(records, sys.stdout, indent=2)
        print()
    else:
        print('\t'.join(headers))
        for row in data:
            print('\t'.join('' if v is None else str(v) for v in row))


if __name__ == '__main__':
    main()
