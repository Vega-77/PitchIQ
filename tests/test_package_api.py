"""`import pitchiq` offers the real entry points and still loads no torch.

The test install has no torch (see `tests/test_requirements.py`), so an import
that pulled it in would fail there; this runs the import in a fresh interpreter
and says so directly, on a machine that does have torch too.
"""

import subprocess
import sys
import unittest

import pitchiq

PROBE = (
    "import sys, pitchiq; print(sorted(m for m in ('torch', 'ultralytics') if m in sys.modules))"
)


class TestThePackageApi(unittest.TestCase):
    def test_every_exported_name_exists(self):
        for name in pitchiq.__all__:
            self.assertTrue(hasattr(pitchiq, name), name)

    def test_the_entry_points_are_exported(self):
        for name in ('analyse_match', 'MatchReport', 'Calibration', 'Pitch', 'PannedCamera'):
            self.assertIn(name, pitchiq.__all__)

    def test_importing_the_package_loads_no_detector_stack(self):
        out = subprocess.run(
            [sys.executable, '-c', PROBE], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(out, '[]')


if __name__ == '__main__':
    unittest.main()
