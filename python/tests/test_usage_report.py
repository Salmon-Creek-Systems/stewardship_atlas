"""scripts/usage_report.py: parsing the usage log format and the SCS/scanner filter."""
import gzip
import os
import sys
import tempfile
import unittest
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
import usage_report as ur


def line(ip="203.0.113.5", request="GET / HTTP/1.1", status=200,
         host="fireatlas.org", owner="-", day="15/Oct/2026"):
    return (f'{ip} - - [{day}:10:00:00 +0000] "{request}" {status} 512 "-" '
            f'"Mozilla/5.0" {host} "{owner}"\n')


class TestParse(unittest.TestCase):

    def test_fields(self):
        f = ur.parse(line(request="GET /scvfd/ HTTP/1.1", host="scvfd.fireatlas.org", owner="1"))
        self.assertEqual(f["ip"], "203.0.113.5")
        self.assertEqual(f["host"], "scvfd.fireatlas.org")
        self.assertEqual(f["owner"], "1")
        self.assertEqual(ur.request_path(f["request"]), "/scvfd/")

    def test_stock_combined_line_is_unparsed(self):
        self.assertIsNone(ur.parse('1.2.3.4 - - [15/Oct/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 5 "-" "x"\n'))

    def test_malformed_request(self):
        f = ur.parse(line(request="\\x16\\x03\\x01"))
        self.assertEqual(ur.request_path(f["request"]), "")
        self.assertFalse(ur.is_probe(f["request"]))


class TestClassify(unittest.TestCase):

    def test_probes(self):
        for path in ["/.env", "/zend/.env", "/wp-includes/ID3/about.php", "/dpack.php",
                     "/.git/config", "/cgi-bin/luci", "/phpmyadmin/"]:
            self.assertTrue(ur.is_probe(f"GET {path} HTTP/1.1"), path)

    def test_real_pages_are_not_probes(self):
        for path in ["/", "/scvfd/staging/outlets/webmap/", "/local/documents/help/index.html",
                     "/staging/outlets/gazetteer/individual_pages/6_b.pdf", "/?src=fair2026",
                     "/.well-known/acme-challenge/abc"]:
            self.assertFalse(ur.is_probe(f"GET {path} HTTP/1.1"), path)

    def test_owner_by_cookie_or_address(self):
        self.assertTrue(ur.is_owner(ur.parse(line(owner="1")), set()))
        self.assertTrue(ur.is_owner(ur.parse(line(ip="198.51.100.9")), {"198.51.100.9"}))
        self.assertFalse(ur.is_owner(ur.parse(line()), {"198.51.100.9"}))

    def test_qr_tag(self):
        self.assertEqual(ur.qr_tag("GET /?src=fair2026 HTTP/1.1"), "fair2026")
        self.assertEqual(ur.qr_tag("GET /scvfd/?a=1&src=rig HTTP/1.1"), "rig")
        self.assertIsNone(ur.qr_tag("GET / HTTP/1.1"))


class TestFilter(unittest.TestCase):

    def test_two_pass_filter_over_gz_and_plain(self):
        with tempfile.TemporaryDirectory() as d:
            plain = os.path.join(d, "usage.log")
            zipped = os.path.join(d, "usage.log.1.gz")
            with open(plain, "w") as f:
                f.write(line(ip="192.0.2.1", request="GET / HTTP/1.1"))       # scanner, before it probes
                f.write(line(owner="1"))                                        # SCS
                f.write(line(ip="203.0.113.7", request="GET /?src=fair HTTP/1.1"))
                f.write("garbage\n")
            with gzip.open(zipped, "wt") as f:
                f.write(line(ip="192.0.2.1", request="GET /.env HTTP/1.1"))   # the probe
                f.write(line(ip="203.0.113.7", request="GET /?src=fair HTTP/1.1"))

            paths = [plain, zipped]
            scanners = ur.scanner_ips(paths)
            self.assertEqual(scanners, {"192.0.2.1"})

            counts = defaultdict(int)
            qr = defaultdict(lambda: {"hits": 0, "visitors": set(), "hosts": set(), "last": ""})
            kept = list(ur.filter_lines(paths, set(), scanners, counts, qr))

            self.assertEqual(len(kept), 2)
            self.assertEqual(dict(counts), {"scanner": 2, "scs": 1, "kept": 2, "unparsed": 1})
            self.assertEqual(qr["fair"]["hits"], 2)
            self.assertEqual(len(qr["fair"]["visitors"]), 1)  # same address, same day


if __name__ == '__main__':
    unittest.main()
