"""A run that stops, waits or finishes tells whoever runs the PC, and never fails for it."""

import json
from types import SimpleNamespace

import pytest

from pitchiq.cli import notify
from pitchiq.cli import process_match as pm
from pitchiq.cli import resume


@pytest.fixture
def net(monkeypatch):
    """urlopen faked: keeps each request and answers 200, or raises `sent.fail`."""
    sent = SimpleNamespace(requests=[], fail=None)

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        if sent.fail:
            raise sent.fail
        sent.requests.append(req)
        return Reply()

    monkeypatch.setattr(notify.urllib.request, 'urlopen', urlopen)
    return sent


@pytest.fixture
def phone(tmp_path, monkeypatch):
    cfg = tmp_path / 'notify.json'
    cfg.write_text(json.dumps({'server': 'https://ntfy.example', 'topic': 'pitchiq-secret'}))
    monkeypatch.setattr(notify, 'CONFIG', cfg)
    return cfg


def test_nothing_is_sent_before_setup(tmp_path, monkeypatch, net):
    monkeypatch.setattr(notify, 'CONFIG', tmp_path / 'none.json')
    assert 'notify --setup' in notify.push('t', 'x')
    assert net.requests == []


def test_a_push_goes_to_the_topic_with_its_priority(phone, net):
    assert notify.push('PitchIQ zz: stopped', 'the pass failed twice', 'high') == ''
    (req,) = net.requests
    assert req.full_url == 'https://ntfy.example/pitchiq-secret'
    assert req.data == b'the pass failed twice'
    assert req.get_header('Priority') == '4' and req.get_header('Title') == 'PitchIQ zz: stopped'


def test_a_failed_push_is_logged_not_raised(phone, net, tmp_path):
    net.fail = OSError('no network')
    notify.send(tmp_path / 'run', 'PitchIQ zz: done', 'finished', 'low')
    log = (tmp_path / 'run' / 'notify.log').read_text(encoding='utf-8')
    assert '[low] PitchIQ zz: done: finished' in log and 'not pushed: OSError' in log


class Card:
    """The coach card's progress: what it was last told."""

    def __init__(self):
        self.state, self.message = None, None

    def stopped(self, message):
        self.state, self.message = 'stopped', message

    def finished(self):
        self.state = 'done'


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, 'DATA', tmp_path)
    told = []
    monkeypatch.setattr(
        notify, 'send', lambda d, title, text, p='default': told.append((title, text, p))
    )
    a = SimpleNamespace(
        game='zz', plan=False, video=tmp_path / 'v.mp4', lines='white', no_publish=True
    )
    r = pm.Run(a)
    r.progress = Card()
    return r, told


def test_a_stop_tells_the_phone_and_gives_the_coach_plain_words(run, monkeypatch):
    r, told = run

    def check_video(path):
        raise pm.Stop('v.mp4 is 1280x720 at 25.00 fps', coach='The video is not the export.')

    monkeypatch.setattr(pm, 'check_video', check_video)
    assert pm.run_steps(r) == 1
    assert r.progress.message == 'The video is not the export.'
    ((title, text, p),) = told
    assert title == 'PitchIQ zz: stopped' and '1280x720' in text and p == 'high'


def test_a_step_that_fails_twice_says_so_on_the_card(run):
    r, _ = run
    r.logs.mkdir(parents=True)
    r.launch = lambda name, args: (r.logs / f'{name}.log').write_text('boom') and 1
    with pytest.raises(pm.Stop) as e:
        r.py('map_zz1', 'pitchiq.experiments.wholemap', 'zz1')
    assert e.value.coach == pm.FAILED_TWICE


def test_a_bug_tells_the_phone_then_shows_its_traceback(run, monkeypatch):
    r, told = run
    monkeypatch.setattr(pm, 'check_video', lambda path: {}['missing'])
    with pytest.raises(KeyError):
        pm.run_steps(r)
    assert r.progress.state == 'stopped'
    assert told[0][0] == 'PitchIQ zz: crashed' and told[0][2] == 'high'


def test_a_plan_tells_nobody(run, monkeypatch):
    r, told = run
    r.a.plan = True
    r.tell('done', 'x')
    assert told == []


def test_a_resume_tells_the_phone(tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(notify, 'send', lambda d, title, text, p='default': told.append((title, p)))
    run_dir = tmp_path / 'stats' / 'zz' / 'run'
    run_dir.mkdir(parents=True)
    resume.note(run_dir, 'NOT RESUMED: cut off 4 times', 'NOT resumed', 'high')
    resume.note(run_dir, 'a plain line')
    assert told == [('PitchIQ zz: NOT resumed', 'high')]
