"""The requirements files must agree about the versions they share.

`requirements-test.txt` exists so CI does not install torch to run tests that
never touch it. The risk that creates is drift: a pin bumped in one file and not
the other means CI is testing against a different numpy than anyone develops on,
and the failure mode is a green CI and a broken machine — or worse, the reverse,
which is the kind of thing that gets a test suite ignored.

`requirements-balltrack.txt` is the ball tracker's extras on top of
requirements.txt, which it includes (`-r`); the same rule holds for it.

Read from the files rather than hard-coded here: a copy of the answer
written down in a third place would just be one more thing to drift.

Run:  .venv/Scripts/python.exe -m pytest tests/test_requirements.py -q
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = REPO / 'requirements.txt'
TEST = REPO / 'requirements-test.txt'
BALL = REPO / 'requirements-balltrack.txt'


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

        mismatched = {name: (main[name], test[name]) for name in shared if main[name] != test[name]}
        assert not mismatched, 'requirements.txt and requirements-test.txt disagree:\n' + '\n'.join(
            f'  {n}: {a} vs {b}' for n, (a, b) in mismatched.items()
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
        for package in ('numpy', 'opencv-python', 'onnxruntime', 'scipy', 'networkx'):
            assert package in pins(TEST), package


class TestBalltrack:
    def test_it_builds_on_the_base_install(self):
        lines = [ln.strip() for ln in BALL.read_text(encoding='utf-8').splitlines()]
        assert '-r requirements.txt' in lines

    def test_it_pins_what_the_tracker_imports(self):
        for package in ('xgboost', 'matplotlib', 'pillow', 'einops'):
            assert package in pins(BALL), package

    def test_nothing_it_pins_disagrees_with_the_other_files(self):
        ball = pins(BALL)
        for path in (MAIN, TEST):
            other = pins(path)
            clash = {n: (ball[n], other[n]) for n in set(ball) & set(other) if ball[n] != other[n]}
            assert not clash, (path.name, clash)

    def test_it_names_the_tapnet_commit_and_the_checkpoint(self):
        text = BALL.read_text(encoding='utf-8')
        assert 'checkout 730cda1' in text
        assert 'tapnextpp_512.ckpt' in text


class TestPyproject:
    """`pip install -e .[extra]` installs the same versions as the three files.

    pyproject.toml is a fourth copy of the pins, so it gets the same rule: every
    package it pins agrees with every file that pins it, and each tier keeps the
    shape the files give it.
    """

    @staticmethod
    def project() -> dict:
        import tomllib

        return tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))['project']

    @staticmethod
    def specs(lines: list[str]) -> dict[str, str]:
        """{package: version} for the `name==version` entries of a dependency list."""
        out = {}
        for spec in lines:
            if '==' in spec:
                name, version = spec.split('==', 1)
                out[name.strip().lower()] = version.strip()
        return out

    def test_no_pin_disagrees_with_any_requirements_file(self):
        project = self.project()
        pinned = self.specs(project['dependencies'])
        for extra in project['optional-dependencies'].values():
            pinned.update(self.specs(extra))
        for path in (MAIN, TEST, BALL):
            other = pins(path)
            clash = {
                n: (pinned[n], other[n]) for n in set(pinned) & set(other) if pinned[n] != other[n]
            }
            assert not clash, (path.name, clash)

    def test_the_base_and_test_tiers_are_the_test_install(self):
        project = self.project()
        light = self.specs(project['dependencies']) | self.specs(
            project['optional-dependencies']['test']
        )
        assert light == pins(TEST)

    def test_the_detector_is_an_extra_not_a_dependency(self):
        project = self.project()
        base = self.specs(project['dependencies'])
        assert 'torch' not in base and 'ultralytics' not in base
        assert self.specs(project['optional-dependencies']['detect']).keys() == (
            pins(MAIN).keys() - base.keys()
        )

    def test_the_balltrack_extra_is_the_balltrack_file(self):
        extra = self.project()['optional-dependencies']['balltrack']
        assert 'pitchiq[detect]' in extra
        assert self.specs(extra) == pins(BALL)
