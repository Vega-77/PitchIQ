"""On GitHub Actions, every failing test also becomes an annotation (an ::error workflow command).

A run's log needs a GitHub sign-in to read, but its annotations are public (the check-runs API),
so a red run says which tests failed and why without one. Nothing changes elsewhere.
(Session step 3.17, 2026-11-24: the Python job had been red since 6 Nov with no readable cause.)
"""

import os

MAX_CHARS = 2000  # of each failure's report, from its end (where the assertion and error are)
_config = []


def pytest_configure(config):
    _config.append(config)


def _annotate(path, line, title, report):
    if os.environ.get('GITHUB_ACTIONS') != 'true' or not report.failed:
        return
    text = '%s\n%s' % (title, report.longreprtext[-MAX_CHARS:])
    text = text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    # the terminal writer, not print: pytest's capture holds stdout while a test runs
    term = _config[0].pluginmanager.get_plugin('terminalreporter') if _config else None
    if term is not None:  # on a line of its own: -q's progress dots leave the line open
        term.write('\n')
        term.write_line('::error file=%s,line=%d::%s' % (path, line, text))


def pytest_runtest_logreport(report):
    path, line, _ = report.location
    _annotate(path, (line or 0) + 1, '%s (%s)' % (report.nodeid, report.when), report)


def pytest_collectreport(report):
    """A file that fails to import (a missing package, say) is a collection error, not a test."""
    _annotate(report.nodeid or '.', 1, '%s (collecting)' % report.nodeid, report)
