"""scripts/easement_986_236: the plain-geometry pieces (bearings, traverse, rigid fit,
cross-sections) and a guard on the transcribed courses."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts', 'easement_986_236'))
import easement_986_236 as ez


class TestBearings(unittest.TestCase):
    def test_quadrants(self):
        self.assertAlmostEqual(ez.az_deg('N 30-00-00 E'), 30)
        self.assertAlmostEqual(ez.az_deg('S 30-00-00 E'), 150)
        self.assertAlmostEqual(ez.az_deg('S 30-00-00 W'), 210)
        self.assertAlmostEqual(ez.az_deg('N 30-00-00 W'), 330)

    def test_minutes_seconds_and_cardinal(self):
        self.assertAlmostEqual(ez.az_deg('N 89-24-58 W'), 360 - (89 + 24 / 60 + 58 / 3600))
        self.assertEqual(ez.az_deg('W'), 270)          # course 26 reads "West" on the map


class TestWalk(unittest.TestCase):
    SQUARE = [(0, 100), (90, 100), (180, 100), (270, 100)]

    def test_closed_square_returns_to_start(self):
        pts = ez.walk((5, 7), self.SQUARE)
        self.assertAlmostEqual(pts[-1][0], 5)
        self.assertAlmostEqual(pts[-1][1], 7)
        self.assertAlmostEqual(pts[1][1], 107)        # first leg due north

    def test_backward_walk_ends_at_start(self):
        courses = [(45, 100), (120, 50)]
        back = ez.walk((0, 0), courses, backward=True)
        fwd = ez.walk(back[0], courses)
        self.assertAlmostEqual(back[-1][0], 0)
        self.assertAlmostEqual(back[-1][1], 0)
        for p, q in zip(back, fwd):
            self.assertAlmostEqual(p[0], q[0])
            self.assertAlmostEqual(p[1], q[1])

    def test_rotation(self):
        pts = ez.walk((0, 0), [(0, 100)], rot_deg=90)
        self.assertAlmostEqual(pts[1][0], 100)
        self.assertAlmostEqual(pts[1][1], 0)


class TestFit(unittest.TestCase):
    def test_icp_recovers_known_rotation_and_shift(self):
        # A zigzag, so along-track sliding can't fake a fit.
        shape = [(0, 0), (300, 80), (500, -60), (800, 120), (1000, 0)]
        theta, tx, ty = math.radians(1.2), 50.0, -20.0
        target = [ez.apply_rigid(shape, theta, tx, ty)]
        got = ez.icp_fit(ez.densify(shape, 10), target, iters=60)
        self.assertAlmostEqual(math.degrees(got[0]), 1.2, delta=0.05)
        self.assertAlmostEqual(got[1], tx, delta=2)
        self.assertAlmostEqual(got[2], ty, delta=2)


class TestCrossSection(unittest.TestCase):
    REF = ez.densify([(0, 0), (0, 200)], 5)          # due north

    def test_parallel_road_offset_is_signed(self):
        off, _ = ez.cross_section(self.REF, 20, [[(12, -50), (12, 300)]])
        self.assertAlmostEqual(off, 12)                # east = right of northbound travel
        off, _ = ez.cross_section(self.REF, 20, [[(-7, -50), (-7, 300)]])
        self.assertAlmostEqual(off, -7)

    def test_side_road_is_ignored(self):
        side = [[(0, 100), (140, 100)]]                # perpendicular spur
        off, _ = ez.cross_section(self.REF, 20, side)
        self.assertIsNone(off)


class TestTranscribedCourses(unittest.TestCase):
    """Net displacement of the transcribed courses, as run on 2026-11-20. A change here
    means courses.csv was edited; re-check it against Parcel Map No. 22 sheet 4 and the grant."""

    def net(self, courses):
        p = ez.walk((0, 0), courses)[-1]
        return p

    def test_courses_unchanged(self):
        pm22, grant = ez.read_courses()
        self.assertEqual((len(pm22), len(grant)), (26, 8))
        e, n = self.net(pm22)
        self.assertAlmostEqual(e, -4169.4, delta=0.1)
        self.assertAlmostEqual(n, -1402.6, delta=0.1)
        e, n = self.net(grant)
        self.assertAlmostEqual(e, -1344.2, delta=0.1)
        self.assertAlmostEqual(n, -242.6, delta=0.1)


if __name__ == '__main__':
    unittest.main()
