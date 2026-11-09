"""What a process_match run tells the coach page: steps, the time left, the requests."""

from __future__ import annotations

import pytest

from pitchiq import run_progress as rp
from pitchiq.cli import process_match as pm

# ---------------------------------------------------------------- a fake Firestore


class Snap:
    def __init__(self, ref):
        self.reference, self.id = ref, ref.id
        self.exists = ref.data is not None

    def to_dict(self):
        return dict(self.ref_data) if self.exists else None

    @property
    def ref_data(self):
        return self.reference.data


class Doc:
    def __init__(self, id_):
        self.id, self.data, self.kids, self.sets = id_, None, {}, []

    def collection(self, name):
        return self.kids.setdefault(name, Coll())

    def get(self):
        return Snap(self)

    def set(self, data):
        self.data = dict(data)
        self.sets.append(dict(data))


class Coll:
    def __init__(self):
        self.docs = {}

    def document(self, id_):
        return self.docs.setdefault(id_, Doc(id_))

    def stream(self):
        return [Snap(d) for d in self.docs.values() if d.data is not None]


class FakeDb(Coll):
    def collection(self, name):
        return self.document('_root').collection(name)


def match(db, team, m, **fields):
    t = db.collection('teams').document(team)
    t.data = t.data or {'name': team}
    doc = t.collection('matches').document(m)
    doc.data = fields
    return doc


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- the steps


def test_the_step_list_is_the_one_the_page_reads():
    keys = [s['key'] for s in rp.load_steps()]
    assert keys[0] == 'halves' and keys[-1] == 'publish'
    assert keys.index('panorama_h1') < keys.index('pins') < keys.index('pass_h1')
    assert all(s['min'][0] <= s['min'][1] for s in rp.load_steps())


@pytest.mark.parametrize(
    'name, key',
    [
        ('halves', 'halves'),
        ('pano_wb1', 'panorama_h1'),
        ('pass_wb2', 'pass_h2'),
        ('map_co1', 'map_h1'),
        ('stats_co2', 'stats_h2'),
        ('transfer_wb2', 'calibrate'),
        ('pincal_wb1', 'calibrate'),
        ('cal_health_wb1', 'calibrate'),
        ('merge', 'publish'),
        ('publish', 'publish'),
        ('something_else', None),
    ],
)
def test_each_command_lands_on_a_page_step(name, key):
    assert rp.step_of(name) == key


def test_every_mapped_step_is_on_the_list():
    keys = {s['key'] for s in rp.load_steps()}
    for name in ('halves', 'pano_xx1', 'pano_xx2', 'pass_xx1', 'map_xx2', 'stats_xx1', 'merge'):
        assert rp.step_of(name) in keys


# ---------------------------------------------------------------- the pass's own pace


def test_a_pass_log_says_how_far_the_half_is(tmp_path):
    log = tmp_path / 'log.txt'
    assert rp.pass_fraction(log) is None  # no log yet
    log.write_text('[10:00] starting\n')
    assert rp.pass_fraction(log) is None  # not sized yet
    lines = ['[10:00] HALF wb1  1000..1999  4 pieces', '[10:05] players 1000..1499 features']
    log.write_text('\n'.join(lines) + '\n')
    assert rp.pass_fraction(log) == pytest.approx(0.1)  # half the features
    lines += ['[10:09] players 1500..1999 features', '[10:20] q0 shipped 250 frames']
    lines += ['[10:30] q0 hybrid done', '[10:31] q1 shipped 250 frames']
    log.write_text('\n'.join(lines) + '\n')
    assert rp.pass_fraction(log) == pytest.approx(0.2 + 0.4 * 2 / 4 + 0.4 * 1 / 4)
    log.write_text('\n'.join([*lines, '[11:00] HALF DONE']) + '\n')
    assert rp.pass_fraction(log) == 1.0


def test_a_restarted_pass_counts_from_its_last_header(tmp_path):
    log = tmp_path / 'log.txt'
    old = ['[09:00] HALF wb1  1000..1999  4 pieces', '[09:05] players 1000..1999 features']
    new = ['[10:00] HALF wb1  1000..1999  4 pieces']
    log.write_text('\n'.join(old + new) + '\n')
    assert rp.pass_fraction(log) == 0.0


# ---------------------------------------------------------------- the time left


def test_without_a_site_nothing_is_written():
    p = rp.Progress(None, 'T', 'M')
    p.begin('pass_h1')
    p.finished()
    assert p.ref is None


def test_time_left_follows_the_expected_times_then_the_steps_own_pace():
    clock = Clock()
    p = rp.Progress(None, None, None, clock=clock)
    p.begin('pass_h2')
    later = sum(p.expected(k) for k in p.keys[p.keys.index('pass_h2') + 1 :])
    assert p.left_s() == pytest.approx(later + p.expected('pass_h2'))
    clock.t += 20 * 60
    p.at(0.05)  # too little to trust: still the expected time, less what went
    assert p.left_s() == pytest.approx(later + p.expected('pass_h2') - 20 * 60)
    p.at(0.5)  # half done in 20 min: 20 more
    assert p.left_s() == pytest.approx(later + 20 * 60)
    clock.t += 10 * 3600  # far over: a sliver, never below nothing
    p.fraction = 0.0
    assert p.left_s() == pytest.approx(later + 0.1 * p.expected('pass_h2'))


def test_a_step_marks_the_ones_before_it_done():
    p = rp.Progress(None, None, None, clock=Clock())
    p.begin('map_h1')
    assert p.done == p.keys[: p.keys.index('map_h1')]
    p.waiting_for_pins(['h2'], 'the other pins disagree')
    d = p.doc()
    assert d['state'] == 'pins' and d['step'] == 'pins' and d['pinHalves'] == ['h2']
    assert d['etaAt'] is None  # no clock time while it waits for the coach
    p.finished()
    d = p.doc()
    assert d['state'] == 'done' and d['done'] == p.keys and d['leftMin'] == 0


def test_writes_are_throttled_but_changes_of_state_are_not():
    clock, db = Clock(), FakeDb()
    match(db, 'T', 'M', opponentName='X')
    p = rp.Progress(db, 'T', 'M', clock=clock)
    ref = p.ref
    p.begin('pass_h1')
    assert len(ref.sets) == 1 and ref.data['step'] == 'pass_h1'
    p.at(0.2)
    clock.t += 30
    p.at(0.3)
    assert len(ref.sets) == 1  # within the minute
    clock.t += 31
    p.at(0.4)
    assert len(ref.sets) == 2 and ref.data['fraction'] == 0.4
    p.stopped('')
    assert len(ref.sets) == 3 and ref.data['state'] == 'stopped'
    assert 'updatedAt' in ref.data


def test_a_failed_write_never_stops_the_run(capsys):
    class Broken:
        def set(self, _):
            raise RuntimeError('offline')

    p = rp.Progress(None, None, None, clock=Clock())
    p.ref = Broken()
    p.begin('pass_h1')
    assert 'not written to the site' in capsys.readouterr().out


# ---------------------------------------------------------------- coach requests


def test_requests_list_the_matches_asked_for_and_not_done():
    db = FakeDb()
    asked = match(db, 'T', 'M1', opponentName='Woodbridge', date='2026-11-01', videoUrl='yt')
    asked.collection('cvRun').document('request').data = {'ours': 'darker'}
    match(db, 'T', 'M2', opponentName='Not asked')
    done = match(db, 'T', 'M3', opponentName='Finished')
    done.collection('cvRun').document('request').data = {'ours': 'lighter'}
    done.collection('cvRun').document('progress').data = {'state': 'done'}
    going = match(db, 'T', 'M4', opponentName='Running')
    going.collection('cvRun').document('request').data = {'ours': 'lighter'}
    going.collection('cvRun').document('progress').data = {'state': 'pins'}
    out = {q['match']: q for q in rp.read_requests(db)}
    assert set(out) == {'M1', 'M4'}
    assert out['M1'] == dict(
        team='T', match='M1', opponent='Woodbridge', date='2026-11-01', video='yt',
        ours='darker', state='not started',
    )  # fmt: skip
    assert out['M4']['state'] == 'pins'


def test_a_game_code_is_suggested_from_the_opponent():
    assert pm.suggest_code('Woodbridge', set()) == 'wo'
    assert pm.suggest_code('Woodbridge', {'wo'}) == 'woo'
    assert pm.suggest_code('St. Joe’s', set()) == 'st'
    assert pm.suggest_code('X', set()) == 'xz'
    assert pm.suggest_code('', set()) == 'ga'


def test_ours_comes_from_the_coachs_request(monkeypatch):
    from pitchiq import publish_to_firestore

    db = FakeDb()
    m = match(db, 'T', 'M', opponentName='Woodbridge', date='2026-11-01')
    monkeypatch.setattr(publish_to_firestore, '_client', lambda: db)
    a = pm.build_parser().parse_args(['v.mp4', '--game', 'xx', '--team', 'T', '--match', 'M'])
    with pytest.raises(pm.Stop, match='--ours is needed'):
        pm.check_site(a)
    m.collection('cvRun').document('request').data = {'ours': 'lighter'}
    assert pm.check_site(a) is db and a.ours == 'lighter'


def test_ours_given_wins_over_the_request(monkeypatch):
    from pitchiq import publish_to_firestore

    db = FakeDb()
    m = match(db, 'T', 'M', opponentName='Woodbridge')
    m.collection('cvRun').document('request').data = {'ours': 'lighter'}
    monkeypatch.setattr(publish_to_firestore, '_client', lambda: db)
    argv = ['v.mp4', '--game', 'xx', '--team', 'T', '--match', 'M', '--ours', 'darker']
    a = pm.build_parser().parse_args(argv)
    pm.check_site(a)
    assert a.ours == 'darker'


@pytest.mark.parametrize(
    'argv',
    [
        ['v.mp4', '--game', 'xx'],  # no site to ask
        ['v.mp4', '--game', 'xx', '--team', 'T', '--match', 'M', '--plan'],  # a plan reads none
    ],
)
def test_without_the_site_ours_must_be_given(argv):
    with pytest.raises(SystemExit):
        pm.main(argv)
