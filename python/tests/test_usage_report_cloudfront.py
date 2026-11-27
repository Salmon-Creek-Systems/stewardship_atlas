"""scripts/usage_report.py --source cloudfront: CloudFront standard logs become
usage-format lines (only the fa_owner cookie kept), cached and stored in S3."""
import datetime as dt
import gzip
import os
import shutil
import sys
import tempfile
import unittest
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
sys.path.insert(0, os.path.dirname(__file__))
import usage_report as ur  # noqa: E402
from fake_s3 import FakeS3  # noqa: E402

CF_FIELDS = ("date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem "
             "sc-status cs(Referer) cs(User-Agent) cs-uri-query cs(Cookie) "
             "x-edge-result-type x-edge-request-id x-host-header cs-protocol cs-bytes "
             "time-taken x-forwarded-for ssl-protocol ssl-cipher x-edge-response-result-type "
             "cs-protocol-version fle-status fle-encrypted-fields c-port time-to-first-byte "
             "x-edge-detailed-result-type sc-content-type sc-content-len sc-range-start "
             "sc-range-end")


def cf_record(stem="/kennedy/current/outlets/webmap/index.html", query="-", cookie="-",
              ip="203.0.113.5", status="200", referer="-",
              agent="Mozilla/5.0%20(iPhone;%20CPU%20iPhone%20OS%2018_0)"):
    values = {"date": "2026-11-27", "time": "21:30:20", "x-edge-location": "SFO5-P1",
              "sc-bytes": "5120", "c-ip": ip, "cs-method": "GET",
              "cs(Host)": "d1y2f6bmf8ds3m.cloudfront.net", "cs-uri-stem": stem,
              "sc-status": status, "cs(Referer)": referer, "cs(User-Agent)": agent,
              "cs-uri-query": query, "cs(Cookie)": cookie, "x-host-header": "fireatlas.org",
              "cs-protocol-version": "HTTP/2.0"}
    return "\t".join(values.get(f, "-") for f in CF_FIELDS.split())


def cf_log(*records):
    return ["#Version: 1.0\n", f"#Fields: {CF_FIELDS}\n"] + [r + "\n" for r in records]


def empty_qr():
    return defaultdict(lambda: {"hits": 0, "visitors": set(), "hosts": set(), "last": ""})


class TestConversion(unittest.TestCase):

    def convert(self, **kw):
        [out] = list(ur.convert_cloudfront_log(cf_log(cf_record(**kw))))
        return out

    def test_a_record_becomes_a_usage_line(self):
        f = ur.parse(self.convert(query="src=fair2026"))
        self.assertEqual(f["ip"], "203.0.113.5")
        self.assertEqual(f["time"], "27/Nov/2026:21:30:20 +0000")
        self.assertEqual(f["request"],
                         "GET /kennedy/current/outlets/webmap/index.html?src=fair2026 HTTP/2.0")
        self.assertEqual(f["status"], "200")
        self.assertEqual(f["host"], "fireatlas.org")    # the viewer's host, not cloudfront.net
        self.assertEqual(f["agent"], "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)")
        self.assertEqual(f["owner"], "-")
        self.assertEqual(ur.qr_tag(f["request"]), "fair2026")

    def test_only_fa_owner_survives_from_the_cookies(self):
        cookie = ("atlas_session=eyJraWQiOi.secret.sig;%20fa_owner=1;%20"
                  "CloudFront-Signature=abc~def")
        out = self.convert(cookie=cookie)
        self.assertEqual(ur.parse(out)["owner"], "1")
        for secret in ("eyJraWQiOi", "atlas_session", "CloudFront-Signature", "abc~def"):
            self.assertNotIn(secret, out)

    def test_other_cookies_alone_are_not_an_owner(self):
        self.assertEqual(ur.parse(self.convert(cookie="atlas_session=x"))["owner"], "-")

    def test_converted_lines_go_through_the_same_filters(self):
        lines = list(ur.convert_cloudfront_log(cf_log(
            cf_record(ip="198.51.100.9", stem="/.env"),
            cf_record(ip="198.51.100.9"),
            cf_record(cookie="fa_owner=1"),
            cf_record())))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.log")
            with open(path, "w") as fh:
                fh.writelines(lines)
            scanners = ur.scanner_ips([path])
            counts = defaultdict(int)
            kept = list(ur.filter_lines([path], set(), scanners, counts, empty_qr()))
        self.assertEqual(scanners, {"198.51.100.9"})
        self.assertEqual((counts["scanner"], counts["scs"], counts["kept"]), (2, 1, 1))
        self.assertEqual(len(kept), 1)

    def test_quotes_in_a_referer_cannot_break_the_line(self):
        self.assertIsNotNone(ur.parse(self.convert(referer='https://x.test/%22a%22')))

    def test_a_malformed_record_is_skipped(self):
        self.assertEqual(list(ur.convert_cloudfront_log(cf_log("2026-11-27\tshort"))), [])

    def test_log_day(self):
        self.assertEqual(ur.log_day("E15MW3URKTZ7Q1.2026-11-27-21.a1b2c3d4.gz"), dt.date(2026, 11, 27))
        self.assertIsNone(ur.log_day("notes.txt"))


class PagingFakeS3(FakeS3):
    def get_paginator(self, name):
        s3 = self

        class Paginator:
            def paginate(self, Bucket, Prefix=""):
                token = None
                while True:
                    page = s3.list_objects_v2(Bucket=Bucket, Prefix=Prefix, ContinuationToken=token)
                    yield page
                    if not page.get("IsTruncated"):
                        return
                    token = page["NextContinuationToken"]
        return Paginator()

    def download_file(self, Bucket, Key, Filename):
        with open(Filename, "wb") as f:
            f.write(self.objects[(Bucket, Key)]["body"])


class TestSync(unittest.TestCase):
    NAME = "E15MW3URKTZ7Q1.2026-11-27-21.a1b2c3d4.gz"

    def setUp(self):
        self.s3 = PagingFakeS3()
        self.cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        body = gzip.compress("".join(cf_log(cf_record(cookie="atlas_session=tok"))).encode())
        self.s3.put_object(Bucket="logs", Key="cloudfront/" + self.NAME, Body=body)

    def sync(self, today=None):
        return ur.sync_cloudfront_logs(self.s3, "logs", "priv", self.cache, today=today)

    def test_a_raw_log_is_cleaned_cached_and_uploaded(self):
        paths, converted = self.sync()
        self.assertEqual(converted, 1)
        key = ("priv", "usage/logs/" + self.NAME)
        self.assertIn(key, self.s3.objects)
        text = gzip.decompress(self.s3.objects[key]["body"]).decode()
        self.assertIsNotNone(ur.parse(text.splitlines()[0]))
        self.assertNotIn("tok", text)
        self.assertEqual([os.path.basename(p) for p in paths], [self.NAME])

    def test_a_cleaned_log_is_not_converted_again(self):
        self.sync()
        _paths, converted = self.sync()
        self.assertEqual(converted, 0)

    def test_an_empty_cache_is_refilled_from_s3(self):
        self.sync()
        for name in os.listdir(self.cache):
            os.remove(os.path.join(self.cache, name))
        self.s3.delete_object(Bucket="logs", Key="cloudfront/" + self.NAME)  # raw expired
        paths, converted = self.sync()
        self.assertEqual((len(paths), converted), (1, 0))

    def test_the_cache_drops_what_s3_has_expired(self):
        self.sync()
        later = dt.date(2026, 11, 27) + dt.timedelta(days=ur.KEEP_DAYS + 1)
        paths, _ = self.sync(today=later)
        self.assertEqual(paths, [])


if __name__ == "__main__":
    unittest.main()
