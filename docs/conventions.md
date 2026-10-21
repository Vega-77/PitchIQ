# Code conventions

The rules for Python in this repo. `tests/test_naming.py`,
`tests/test_duplicate_bodies_py.py` and CI's ruff steps enforce the ones a
machine can check. The rest are for review.

## Formatting

- `ruff format` decides layout: line length 100, single quotes. Run it before
  committing; CI runs `ruff format --check` and fails on any difference.
- `ruff check` runs the classic pyflakes and pycodestyle error rules
  (`E4`, `E7`, `E9`, `F`), pinned in `pyproject.toml`. Fix findings rather than
  silencing them; a `# noqa` needs a reason on the same line.

## Names

- A filename says what the file does, not which attempt it is. No `2`, `4`,
  `_v3`, `fast`, `new` or `old` suffixes, and no `v`-prefix abbreviations. When
  a better version replaces a script, it takes the name and the old one goes.
- Import aliases are at least three characters. The exceptions are the
  community-standard `np`, `pd`, `mp`, `plt` and `cv2`.
- Units live in names: `_m` metres, `_px` pixels, `_s` seconds, `_ph` player
  heights, `_sb` StatsBomb pitch units (120 x 80). A bare `x` is a position
  whose frame is obvious from the function, or a bug.
- No personal names or machine paths in code, comments or docs. Paths come
  from `PIQ_REPO`, `PIQ_DATA` and `PIQ_VIDEO` (see the README), or are relative
  to the repo.

## Docstrings

A module docstring is:

1. one summary line, at most 90 characters;
2. what the module is for, its inputs and outputs with units, what it
   assumes, and what it gets wrong.

It holds no dates, no names and no "measured X on date Y". That history goes
in `docs/design/<module>.md`, which the docstring may link to.

Every public class, and every function of eight lines or more, has a
docstring. The first line says what the function returns or does.

## Types

Every function in `cv/` is annotated, including its return type
(`-> None` included). Arrays carry their shape and units in the docstring,
for example `(n, 2) float, pixels`.

## Code shape

- One implementation per job. A helper needed by two modules lives in one of
  them, or in `cv/_util.py`, and is imported by the other. The duplicate-body
  tests fail on two functions with the same body.
- A function longer than about 80 lines, or with more than about 10
  parameters, is a candidate for splitting. Group related options into a
  dataclass instead of adding another keyword.
- Report JSON keys are a published contract: never rename one. Fields can be
  added.

## Commits

One change per commit. Behaviour changes and refactors never share a commit.
A refactor proves it changes nothing: the same test results, the same
`report.to_json()`, and byte-identical ball-tracker outputs where it touches
`cv/balltrack/`.
