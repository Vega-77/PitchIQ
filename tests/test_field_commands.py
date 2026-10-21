"""The commands in the field guide have to run when somebody types them.

`FOOTAGE_DAY.md` is written to be read on a phone at the side of a pitch, and
every step in it is a command copied out of a fenced block. Nothing checked that
those commands still matched the code. A renamed flag or a dropped argument
would sit in the guide indefinitely and surface exactly once — on the evening the
first real footage lands, in the dark, with no time to fix it and no second clip
for another week.

So this file reads the Markdown the way a person does: it pulls every
`python -m cv.experiments.*` invocation out of the docs, tokenises it, and hands
the arguments to the parser of the module it names. A flag that no longer exists
fails here instead of there.

There is no video and no model anywhere in this file. Argument parsing does not
open the paths it is given, so `clips/first-half.mp4` never has to exist — which
is the whole reason this check is available months before the footage is.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import pkgutil
import re
import shlex
import unittest
from pathlib import Path

import cv.experiments

REPO = Path(__file__).resolve().parent.parent
PREFIX = 'python -m cv.experiments.'

# Every Markdown file that is allowed to teach somebody a command. Kept explicit
# rather than globbed: a stray README in a scratch directory should not become
# something the test suite defends.
DOCS = ['FOOTAGE_DAY.md', 'ROADMAP.md', 'baselines/README.md']


def module_names() -> list[str]:
    """Every command under `cv/experiments/`, in the order a listing shows them."""
    return sorted(
        info.name
        for info in pkgutil.iter_modules(cv.experiments.__path__)
        if not info.name.startswith('_')
    )


FENCE = re.compile(r'^```[a-z]*\n(.*?)^```', re.S | re.M)


def _commands_in_fences(text: str) -> list[str]:
    """Fenced blocks, with shell line continuations joined back up."""
    out = []
    for block in FENCE.findall(text):
        joined = re.sub(r'\\\n\s*', ' ', block)
        out.extend(
            line.strip() for line in joined.splitlines()
            if line.strip().startswith(PREFIX)
        )
    return out


def _commands_in_prose(text: str) -> list[str]:
    """Backticked spans, which Markdown lets wrap across a line break.

    Fences have to come out first. A ``` fence is three backticks, so scanning
    for single-backtick spans across one pairs the closing backtick of the fence
    with the opening backtick of whatever follows, and every span in the rest of
    the file is off by one. That silently found nothing in a 6000-line ROADMAP
    and reported it as a pass.
    """
    out = []
    for span in re.findall(r'`([^`]+)`', FENCE.sub('', text)):
        span = ' '.join(span.split())
        if span.startswith(PREFIX):
            out.append(span)
    return out


# `python -m cv.experiments.*` and `python -m cv.experiments.<name>` are how
# prose names the set of commands rather than one of them. Only this exact
# placeholder syntax is skipped: a module name that is merely wrong still fails.
PLACEHOLDER = re.compile(re.escape(PREFIX) + r'(\*|<[^>]*>)')


def documented_commands() -> list[tuple[str, str]]:
    """`(source, command)` for every invocation written down anywhere in the docs."""
    found = []
    for name in DOCS:
        # No existence guard on purpose. A renamed guide should fail this test
        # rather than drop out of it, which is what skipping a missing file
        # quietly does.
        text = (REPO / name).read_text(encoding='utf-8')
        for command in _commands_in_fences(text) + _commands_in_prose(text):
            if PLACEHOLDER.match(command):
                continue
            found.append((name, command))
    return found


def split(command: str) -> tuple[str, list[str]]:
    """`(module, args)`. Square brackets around an optional flag are usage
    notation rather than shell syntax, so they come off before parsing."""
    tokens = [t.strip('[]') for t in shlex.split(command)]
    assert tokens[:2] == ['python', '-m']
    return tokens[2].split('.')[-1], tokens[3:]


SHAPES = """\
Prose first, mentioning `python -m cv.experiments.spike_detect` with no
arguments at all.

```bash
python -m cv.experiments.grab_frame clips/x.mp4 --at 60
```

The set as a whole is `python -m cv.experiments.*`, which names no module.

A wrapped span: run `python -m cv.experiments.calibrate
points.json --frame frame.png` and look at the error.

```bash
python -m cv.experiments.spike_detect clips/x.mp4 \\
    --conf 0.08 --tiles 2
```
"""


class TestTheExtractorReadsMarkdownTheWayAPersonDoes(unittest.TestCase):
    """Every shape a command is written in, in one document.

    Worth pinning because the failure mode is silence: an extractor that finds
    nothing reports that every documented command is fine.
    """

    def found(self):
        return [
            c for c in _commands_in_fences(SHAPES) + _commands_in_prose(SHAPES)
            if not PLACEHOLDER.match(c)
        ]

    def test_all_four_are_found_and_none_twice(self):
        self.assertEqual(len(self.found()), 4)

    def test_a_line_continuation_is_joined_back_into_one_command(self):
        joined = [c for c in self.found() if '--tiles' in c]
        self.assertEqual(len(joined), 1)
        self.assertIn('--conf 0.08 --tiles 2', joined[0])

    def test_a_span_wrapped_across_a_line_break_survives(self):
        wrapped = [c for c in self.found() if 'calibrate' in c]
        self.assertEqual(
            wrapped, [PREFIX + 'calibrate points.json --frame frame.png']
        )

    def test_a_glob_naming_the_whole_set_is_not_read_as_a_command(self):
        # Writing about these commands in the docs should not break the check on
        # them, and `cv.experiments.*` is the natural way to write about them.
        self.assertFalse([c for c in self.found() if '.*' in c])
        self.assertTrue(PLACEHOLDER.match(PREFIX + '<name> --conf 0.08'))
        self.assertFalse(PLACEHOLDER.match(PREFIX + 'spike_detect'))

    def test_a_fence_does_not_desynchronise_the_prose_scan(self):
        # Three backticks are three backticks. Scanning for single-backtick
        # spans without removing fences first pairs a fence's closing tick with
        # the next opening one, and every span after the first fence is off by
        # one — which found nothing in a 6000-line ROADMAP and called it a pass.
        bare = [c for c in _commands_in_prose(SHAPES) if 'spike_detect' in c]
        self.assertEqual(bare, [PREFIX + 'spike_detect'])


class TestEveryCommandIsAModule(unittest.TestCase):
    def test_the_docs_are_not_quietly_empty(self):
        # If the extractor breaks, every test below passes vacuously. This is
        # the one that notices.
        self.assertGreaterEqual(len(documented_commands()), 10)

    def test_each_doc_that_teaches_a_command_still_yields_one(self):
        # A per-file floor, not just a total: the total stayed comfortably above
        # ten while one whole file was being read as empty.
        by_source = {source for source, _ in documented_commands()}
        for name in DOCS:
            self.assertIn(name, by_source)

    def test_every_documented_command_names_a_real_module(self):
        known = set(module_names())
        for source, command in documented_commands():
            module, _ = split(command)
            self.assertIn(module, known, f'{source}: {command}')

    def test_every_module_can_be_imported(self):
        # Catches a stale import in a command nobody has run for a month, which
        # is otherwise invisible until the moment it is needed.
        for name in module_names():
            importlib.import_module(f'cv.experiments.{name}')


class TestEveryCommandParses(unittest.TestCase):
    def test_the_documented_arguments_all_exist(self):
        for source, command in documented_commands():
            module, args = split(command)
            if not args:
                continue  # `see \`python -m ...spike_detect\`` is prose, not a command
            parser = importlib.import_module(
                f'cv.experiments.{module}'
            ).build_parser()
            with self.subTest(source=source, command=command):
                try:
                    with contextlib.redirect_stderr(io.StringIO()) as complaint:
                        parser.parse_args(args)
                except SystemExit:
                    self.fail(
                        f'{source} documents a command that no longer parses:\n'
                        f'  {command}\n'
                        f'  {complaint.getvalue().strip()}'
                    )


class TestEveryCommandHasTheSameShape(unittest.TestCase):
    def modules(self):
        return [
            (name, importlib.import_module(f'cv.experiments.{name}'))
            for name in module_names()
        ]

    def test_each_one_exposes_a_parser_and_an_entry_point(self):
        for name, module in self.modules():
            self.assertTrue(
                hasattr(module, 'build_parser'),
                f'{name} builds its parser inside main(), so nothing can ask it '
                f'what flags it takes without running it against a video',
            )
            self.assertIsInstance(module.build_parser(), argparse.ArgumentParser)
            self.assertTrue(hasattr(module, 'main'), name)

    def test_the_usage_line_is_the_command_you_type(self):
        # `usage: track_report [...]` is a command that does not exist. So is
        # `usage: -c`, which is what argparse infers when nothing sets prog.
        for name, module in self.modules():
            self.assertEqual(module.build_parser().prog, PREFIX + name)

    def test_help_prints_rather_than_crashing(self):
        for name, module in self.modules():
            with contextlib.redirect_stdout(io.StringIO()) as text:
                with self.assertRaises(SystemExit) as exit:
                    module.build_parser().parse_args(['--help'])
            self.assertEqual(exit.exception.code, 0, name)
            self.assertIn(PREFIX + name, text.getvalue())

    def test_every_command_describes_itself(self):
        for name, module in self.modules():
            description = module.build_parser().description
            self.assertTrue(description and description.strip(), name)


if __name__ == '__main__':
    unittest.main()
