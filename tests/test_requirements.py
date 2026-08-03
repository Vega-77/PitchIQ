"""The two requirements files must agree about the versions they share.

`requirements-test.txt` exists so CI does not install torch to run tests that
never touch it. The risk that creates is drift: a pin bumped in one file and not
the other means CI is testing against a different numpy than anyone develops on,
and the failure mode is a green CI and a broken machine — or worse, the reverse,
which is the kind of thing that gets a test suite ignored.

Read from the files rather than hard-coded here, for the same reason
tests/test_xg_bridge.py scrapes FEATURES out of main.py: a copy of the answer
written down in a third place would just be one more thing to drift.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_requirements.py -q
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = REPO / 'requirements.txt'
TEST = REPO / 'requirements-test.txt'


def pins(path: Path) -> dict[str, str]:
    """{package: version} for every uncommented `name==version` line."""
    out = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if '==' not in line:
            continue
        name, version = line.split('==', 1)
        out[name.strip().lower()] = version.strip()
    return out


class TestPinsAgree:
    def test_both_files_exist_and_pin_something(self):
        assert pins(MAIN), 'requirements.txt pins nothing'
        assert pins(TEST), 'requirements-test.txt pins nothing'

    def test_shared_packages_are_on_the_same_version(self):
        main, test = pins(MAIN), pins(TEST)
        shared = set(main) & set(test)
        assert shared, 'the two files share no packages, which cannot be right'

        mismatched = {
            name: (main[name], test[name])
            for name in shared if main[name] != test[name]
        }
        assert not mismatched, (
            'requirements.txt and requirements-test.txt disagree:\n'
            + '\n'.join(f'  {n}: {a} vs {b}' for n, (a, b) in mismatched.items())
        )

    def test_the_heavy_dependencies_stay_out_of_the_test_install(self):
        """The whole point of the split.

        If torch ever appears here, either something under test started
        importing a detector for real, or the file was edited by copy-paste.
        Both are worth stopping at.
        """
        assert 'torch' not in pins(TEST)
        assert 'ultralytics' not in pins(TEST)

    def test_everything_the_tests_import_is_pinned_somewhere(self):
        """Guards the other direction: a new third-party import with no pin
        passes locally, where it is already installed, and fails only in CI."""
        for package in ('numpy', 'opencv-python', 'onnxruntime'):
            assert package in pins(TEST), package
