# Contributing

PitchIQ is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE), not open source. Bug reports,
footage that does not work and small fixes are welcome. For anything larger,
open an issue first so the change can be agreed before you spend time on it.

## Set up

Python 3.12, from a clone:

```bash
pip install -e .[test]
python -m pitchiq demo        # the whole pipeline on a scripted clip, in seconds
```

The site needs Node 22.15 or later, and the Firestore rules suite needs a JVM
(the emulator runs on Java). [README.md](README.md#setup) has the full setup,
including the GPU extras for real footage.

## Before you open a pull request

Run what CI runs:

```bash
python -m pytest tests/ -q
python -m pyflakes pitchiq/ tests/
python -m ruff format --check pitchiq tests tools xg_training
python -m ruff check pitchiq tests tools xg_training
npm test
```

If you changed any file the site serves (HTML, CSS or JS under the page
folders or `shared/`), also run `python stamp_version.py <n+1>`, where `n` is
the `?v=` that `index.html` carries now. Browsers cache the old files
otherwise, and `test_version_stamp` fails on a mixed stamp.

## What the tests guard

[tests/README.md](tests/README.md) sorts the suite into unit tests, seam
tests and repo-wide hygiene tests, and says what each hygiene test protects
against. A few will fail on changes that look harmless:

- **Reachability** (`test_call_graph`, `test_member_graph`,
  `test_browser_graph`): a function, method or browser module that nothing
  but tests uses fails the build. Wire it in or delete it.
- **Accessibility** (`test_contrast_floor`, `test_control_names`,
  `test_heading_outline`, `test_landmarks`, `test_skip_link`,
  `test_focus_visible`): contrast is measured from the stylesheets, every
  control needs a name a screen reader can say, and every view needs a
  heading. A new page goes on each of their page lists and in
  `stamp_version.py`.
- **Caps held twice** (`test_cap_pairs`): a limit in `firestore.rules` and
  the client guard that mirrors it have to move together.
- **Naming** (`test_naming`): the checkable rules in
  [docs/conventions.md](docs/conventions.md).

## How changes are made

[docs/conventions.md](docs/conventions.md) is the house style: ruff's
formatter, names, docstrings and commit shape. In short:

- One change per commit. A behaviour change and a refactor never share one.
- A refactor shows it changes nothing: the same test results, the same
  `report.to_json()`, and byte-identical ball-tracker output where it touches
  `pitchiq/balltrack/`.
- `firestore.rules` is the whole security boundary. A rules change comes with
  tests in `tests/rules.test.js` for what must be denied.

## Footage and student data

Match video shows minors. Do not attach video, or frames with faces, names or
shirt names readable, to an issue or pull request. Describe the footage
instead (the issue template asks the useful questions); if a clip is needed,
the maintainer will arrange a private way to share it.

Security problems, above all anything that lets one account read another
team's or another player's data, go by email, not in an issue: see
[SECURITY.md](SECURITY.md).
