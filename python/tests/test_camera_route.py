"""
camera_route: smoothing a line for the 3D view's Animate Camera.

Standard library only, imports only camera_route, so it runs on a bare
checkout (see test_map_style for why that matters, #153).
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import camera_route as cr


def _max_turn(pts):
    return max(cr.turn_deg(pts[i - 1], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1))


class TestTurnDeg(unittest.TestCase):
    def test_straight_right_angle_and_reversal(self):
        self.assertAlmostEqual(cr.turn_deg((0, 0), (1, 0), (2, 0)), 0)
        self.assertAlmostEqual(cr.turn_deg((0, 0), (1, 0), (1, 1)), 90)
        self.assertAlmostEqual(cr.turn_deg((0, 0), (1, 0), (1, -1)), 90)
        self.assertAlmostEqual(cr.turn_deg((0, 0), (1, 0), (0, 0)), 180)


class TestSimplify(unittest.TestCase):
    def test_drops_jitter_keeps_real_corner(self):
        jittery = [(0, 0), (100, 1), (200, -1), (300, 0), (300, 300)]
        self.assertEqual(cr.simplify(jittery, 5), [(0, 0), (300, 0), (300, 300)])

    def test_zero_tolerance_is_identity(self):
        pts = [(0, 0), (100, 1), (200, 0)]
        self.assertEqual(cr.simplify(pts, 0), pts)


class TestRoundCorners(unittest.TestCase):
    L = [(0, 0), (1000, 0), (1000, 1000)]

    def test_turn_at_threshold_is_left_alone(self):
        # 30 degrees exactly is not "more than 30"
        a = math.radians(30)
        pts = [(0, 0), (1000, 0), (1000 + 1000 * math.cos(a), 1000 * math.sin(a))]
        self.assertEqual(cr.round_corners(pts, 30, 150), pts)

    def test_sharp_corner_becomes_gentle_turns(self):
        out = cr.round_corners(self.L, 30, 150)
        self.assertNotIn((1000, 0), out)
        self.assertLess(_max_turn(out), 20)

    def test_curve_starts_and_ends_radius_from_the_corner(self):
        out = cr.round_corners(self.L, 30, 150)
        self.assertIn((850.0, 0.0), out)
        self.assertIn((1000.0, 150.0), out)

    def test_radius_capped_at_half_the_shorter_segment(self):
        short = [(0, 0), (100, 0), (100, 1000)]
        out = cr.round_corners(short, 30, 150)
        self.assertIn((50.0, 0.0), out)
        # every curve point stays inside the corner's bounding box
        for x, y in out:
            self.assertTrue(0 <= x <= 100 and 0 <= y <= 1000)

    def test_endpoints_unchanged(self):
        out = cr.round_corners(self.L, 30, 150)
        self.assertEqual(out[0], self.L[0])
        self.assertEqual(out[-1], self.L[-1])


class TestDensify(unittest.TestCase):
    def test_even_spacing_and_endpoints(self):
        pts = [(0, 0), (100, 0), (100, 50)]
        out = cr.densify(pts, 15)
        gaps = [math.dist(out[i - 1], out[i]) for i in range(1, len(out))]
        self.assertEqual(out[0], (0, 0))
        self.assertAlmostEqual(out[-1][0], 100)
        self.assertAlmostEqual(out[-1][1], 50)
        self.assertLessEqual(max(gaps), 15 + 1e-9)
        # all gaps equal along a straight run (the corner chord is the only short one)
        self.assertGreater(min(gaps), 10)


class TestSmoothLine(unittest.TestCase):
    def test_lnglat_round_trip_keeps_ends(self):
        coords = [[-123.80, 40.10], [-123.79, 40.10], [-123.79, 40.11]]
        out = cr.smooth_line(coords)
        self.assertAlmostEqual(out[0][0], coords[0][0], places=7)
        self.assertAlmostEqual(out[-1][1], coords[-1][1], places=7)
        self.assertGreater(len(out), 50)

    def test_ignores_z(self):
        coords = [[-123.80, 40.10, 300], [-123.79, 40.10, 310]]
        self.assertEqual(len(cr.smooth_line(coords)[0]), 2)


class TestLiftProfile(unittest.TestCase):
    SPACING = 15

    def _dist(self, n):
        return [i * self.SPACING for i in range(n)]

    def test_flat_ground_gets_no_lift(self):
        d = self._dist(200)
        self.assertTrue(all(abs(v) < 1e-9 for v in cr.lift_profile(d, [250.0] * 200)))

    def test_lift_is_never_negative(self):
        d = self._dist(300)
        ground = [100 + 80 * math.sin(i / 7) + (40 if i % 37 == 0 else 0) for i in range(300)]
        self.assertGreaterEqual(min(cr.lift_profile(d, ground)), -1e-9)

    def test_camera_path_respects_max_grade(self):
        d = self._dist(300)
        ground = [100 + 80 * math.sin(i / 7) for i in range(300)]
        lift = cr.lift_profile(d, ground, max_grade=0.15)
        path = [g + l for g, l in zip(ground, lift)]
        for i in range(1, len(path)):
            self.assertLessEqual(abs(path[i] - path[i - 1]), 0.15 * self.SPACING + 1e-6)

    def test_single_ridge_lift_tapers_on_both_sides(self):
        # 200 m ridge with 45% flanks in the middle of flat ground
        n = 400
        d = self._dist(n)
        mid = n // 2
        ground = [max(0.0, 200 - 0.45 * abs(i - mid) * self.SPACING) for i in range(n)]
        lift = cr.lift_profile(d, ground, max_grade=0.15, blur_m=60)
        self.assertLess(lift[mid], 1e-6)              # on the ridge top: no lift needed
        self.assertGreater(lift[mid - 30], 50)         # on the flank: camera held up
        self.assertGreater(lift[mid + 30], 50)
        self.assertLess(lift[0], 1e-6)                 # far away: back to slider height
        self.assertLess(lift[-1], 1e-6)
        # Approaching, the camera starts climbing ~89 samples out (200 m at 15%)
        # while the ground stays flat until the ridge foot ~30 samples out, so
        # lift only grows over that stretch (kept clear of the blur at each end).
        # Up the flank the ground climbs faster than the camera and lift falls to 0.
        rise = lift[mid - 75:mid - 45]
        self.assertTrue(all(b >= a - 1e-6 for a, b in zip(rise, rise[1:])))
        self.assertGreater(lift[mid - 30], lift[mid - 10])


class TestGradeEnvelope(unittest.TestCase):
    def test_is_minimal(self):
        # one spike: the envelope is the spike's cone and nothing more
        d = [0, 10, 20, 30, 40]
        env = cr.grade_envelope(d, [0, 0, 10, 0, 0], 0.5)
        self.assertEqual(env, [0, 5, 10, 5, 0])


class TestTerrarium(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(cr.terrarium_elevation(128, 0, 0), 0)
        self.assertAlmostEqual(cr.terrarium_elevation(129, 44, 128), 300.5)

    def test_tile_pixel_origin_and_center(self):
        self.assertEqual(cr.tile_pixel(-180, 85.05112878, 0)[:2], (0, 0))
        tx, ty, px, py = cr.tile_pixel(0, 0, 1)
        self.assertEqual((tx, ty), (1, 1))
        self.assertAlmostEqual(px, 0)
        self.assertAlmostEqual(py, 0, places=6)


if __name__ == '__main__':
    unittest.main()
