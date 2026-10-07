import numpy as np

from cv.linking import KEEPER, OFF, PLAYER, REFEREE, LinkSettings, Linker
from cv.teams import UNKNOWN
from cv.pitchtrack import Detections, PitchProjector, Tracklet

W, H = 1920, 1080
PX_PER_M = 20.0                       # the picture shows 96 x 54 m
PITCH_L, PITCH_W = 80.0, 40.0          # so there is room beyond every line

DARK = dict(team=1, kit=[30.0, 128.0, 128.0, -100.0], desc=[30.0, 128.0, 128.0, 30.0, 128.0, 128.0])
LIGHT = dict(team=0, kit=[240.0, 128.0, 128.0, 110.0], desc=[240.0, 128.0, 128.0, 60.0, 128.0, 128.0])
NEON = dict(team=0, kit=[245.0, 111.0, 162.0, 115.0], desc=[245.0, 111.0, 162.0, 40.0, 116.0, 140.0])


def projector(n):
    h = np.diag([1 / PX_PER_M, 1 / PX_PER_M, 1.0])
    return PitchProjector({f: h for f in range(n)}, W, H)


def box(xy, height=80.0):
    u, v = xy[0] * PX_PER_M, xy[1] * PX_PER_M
    return [u - height / 4, v - height, u + height / 4, v]


class Scene:
    """People on a flat pitch; each run of consecutive frames is one tracklet."""

    def __init__(self, n):
        self.n = n
        self.rows = []                  # (frame, box, team, kit, desc, person)
        self.runs = []                  # (person, [row indices before sorting])

    def person(self, where, look, frames, team=None):
        """where(f) -> (x, y); frames: iterable of the frames it is detected in."""
        pid = len({r[5] for r in self.rows})
        run, last = [], None
        for f in frames:
            if last is not None and f != last + 1:
                self.runs.append((pid, run))
                run = []
            run.append(len(self.rows))
            self.rows.append((f, box(where(f)), look['team'] if team is None else team,
                              look['kit'], look['desc'], pid))
            last = f
        if run:
            self.runs.append((pid, run))
        return pid

    def build(self, with_kit=False):
        order = sorted(range(len(self.rows)), key=lambda i: self.rows[i][0])
        new = {old: k for k, old in enumerate(order)}
        r = [self.rows[i] for i in order]
        det = Detections(
            frame=[x[0] for x in r], box=[x[1] for x in r], conf=[0.9] * len(r),
            team=[x[2] for x in r], desc=np.array([x[4] for x in r], dtype=np.float64),
            kit=np.array([x[3] for x in r], dtype=np.float64) if with_kit else None)
        tracklets = [Tracklet(i, np.array(sorted(new[k] for k in run)), 'lost', 'new')
                     for i, (_, run) in enumerate(self.runs)]
        owner = [x[5] for x in r]
        return det, tracklets, owner

    def link(self, with_kit=False, **settings):
        det, tracklets, owner = self.build(with_kit)
        res = Linker(projector(self.n), PITCH_L, PITCH_W, LinkSettings(**settings)).link(tracklets, det)
        return res, owner


def people_in(ident, owner):
    return {owner[r] for r in ident.rows if r >= 0}


def walker(x0, y0, vx=0.0, vy=0.0, fps=30.0):
    return lambda f: (x0 + vx * f / fps, y0 + vy * f / fps)


def background(scene, frames):
    """Two still teams of three, so the window has both kits and a defending side."""
    for k in range(3):
        scene.person(walker(25.0, 8.0 + 12 * k), DARK, frames)
        scene.person(walker(55.0, 8.0 + 12 * k), LIGHT, frames)


def test_occluded_player_is_joined_and_the_gap_filled():
    s = Scene(150)
    p = s.person(walker(30.0, 20.0, vx=3.0), DARK, [f for f in range(135) if not 60 <= f < 75])
    res, owner = s.link()
    mine = [i for i in res.identities if p in people_in(i, owner)]
    assert len(mine) == 1
    ident = mine[0]
    assert people_in(ident, owner) == {p}
    gap = (ident.frames >= 60) & (ident.frames < 75)
    assert gap.sum() == 15                         # filled
    assert ident.interp[gap].all() and (ident.conf[gap] == 0).all() and (ident.rows[gap] == -1).all()
    assert not ident.interp[~gap].any()


def test_different_teams_are_never_joined():
    s = Scene(150)
    w = walker(30.0, 20.0, vx=3.0)
    s.person(w, DARK, range(60))
    s.person(w, LIGHT, range(75, 135))
    res, owner = s.link()
    assert len(res.identities) == 2
    assert all(len(people_in(i, owner)) == 1 for i in res.identities)


def test_tracklets_seen_together_are_two_people():
    s = Scene(100)
    s.person(walker(30.0, 20.0), DARK, range(0, 60))
    s.person(walker(30.5, 20.0), DARK, range(50, 100))
    res, _ = s.link()
    assert len(res.identities) == 2


def test_unreachable_gap_is_not_joined():
    s = Scene(150)
    s.person(walker(30.0, 20.0), DARK, range(0, 60))
    s.person(walker(60.0, 20.0), DARK, range(75, 135))    # 30 m in half a second
    res, _ = s.link()
    assert len(res.identities) == 2
    assert not res.joins


def test_a_join_that_could_go_either_way_is_refused():
    s = Scene(100)
    s.person(walker(40.0, 19.0), DARK, range(0, 40))
    s.person(walker(40.0, 21.0), DARK, range(0, 40))
    s.person(walker(41.0, 19.95), DARK, range(50, 100))
    s.person(walker(41.0, 20.05), DARK, range(50, 100))
    res, owner = s.link()
    assert len(res.identities) == 4
    assert res.refused


def test_a_refused_return_is_shown_as_a_question_mark():
    s = Scene(100)
    s.person(walker(40.0, 19.0), DARK, range(0, 40))
    s.person(walker(40.0, 21.0), DARK, range(0, 40))
    s.person(walker(41.0, 19.95), DARK, range(50, 100))
    s.person(walker(41.0, 20.05), DARK, range(50, 100))
    res, owner = s.link()
    late = [i for i in res.identities if i.frames[0] == 50]
    assert len(late) == 2
    assert [res.label(i) for i in late] == ['?', '?']           # a near tie names nobody
    early = [i for i in res.identities if i.frames[0] == 0]
    assert all(res.label(i) == str(i.pid) for i in early)


def test_a_return_too_long_unseen_to_join_is_a_named_guess():
    s = Scene(400)
    background(s, range(400))
    p = s.person(walker(40.0, 20.0), DARK, range(0, 60))
    s.person(walker(40.5, 20.0), DARK, range(270, 400))       # 7 s gap, in view and alone
    res, owner = s.link()
    gone = [i for i in res.identities if people_in(i, owner) == {p}]
    back = [i for i in res.identities if i.frames[0] == 270]
    assert len(gone) == 1 and len(back) == 1                     # too costly to join
    assert res.label(back[0]) == f'{gone[0].pid}?'


def test_detector_features_settle_a_join_that_could_go_either_way():
    s = Scene(100)
    s.person(walker(40.0, 19.0), DARK, range(0, 40))
    s.person(walker(40.0, 21.0), DARK, range(0, 40))
    s.person(walker(41.0, 19.95), DARK, range(50, 100))
    s.person(walker(41.0, 20.05), DARK, range(50, 100))
    det, tracklets, owner = s.build()
    looks = np.eye(4)[[0, 1, 1, 0]]               # the far one at 21 m comes back nearer 19
    det.feat = looks[owner]
    res = Linker(projector(100), PITCH_L, PITCH_W, LinkSettings(feature_weight=2.0)).link(tracklets, det)
    assert sorted(sorted(people_in(i, owner)) for i in res.identities
                  if people_in(i, owner) & {0, 1, 2, 3}) == [[0, 3], [1, 2]]


def test_roles_off_pitch_keeper_and_referee():
    s = Scene(150)
    frames = range(150)
    background(s, frames)
    off = s.person(walker(40.0, PITCH_W + 6.0), DARK, frames)          # beyond the far line
    keeper = s.person(walker(3.0, PITCH_W / 2, vy=0.5), LIGHT, frames)  # in front of the left goal
    ref = s.person(walker(40.0, 15.0, vx=1.0), NEON, frames)
    res, owner = s.link(with_kit=True)
    role = {next(iter(people_in(i, owner))): i for i in res.identities}
    assert role[off].role == OFF
    assert role[keeper].role == KEEPER
    # the dark team stands nearer x=0, so the left keeper is theirs, whatever the shirt
    assert role[keeper].team == role[0].team
    assert role[ref].role == REFEREE and role[ref].team == 'unknown'
    assert sum(i.role == PLAYER for i in res.identities) == 6


def test_the_referee_kit_is_never_joined_to_a_player():
    s = Scene(160)
    frames = range(160)
    background(s, frames)
    w = walker(40.0, 15.0, vx=2.0)
    s.person(w, NEON, range(0, 60))
    s.person(w, LIGHT, range(70, 160))           # where the referee was going, in white
    res, owner = s.link(with_kit=True)
    assert all(len(people_in(i, owner)) == 1 for i in res.identities)


def test_without_fill_gaps_stay_empty():
    s = Scene(150)
    s.person(walker(30.0, 20.0, vx=3.0), DARK, [f for f in range(135) if not 60 <= f < 75])
    res, _ = s.link(fill_gap_s=0.0)
    assert len(res.identities) == 1
    assert not res.identities[0].interp.any()


def test_a_coach_on_the_line_stays_off_and_alone():
    s = Scene(180)
    background(s, range(180))
    short = s.person(walker(40.0, 6.0), DARK, range(0, 10))           # a third of a second, well inside
    # still, then a few steps, then drifting along the line: each piece alone
    # is a still person on the line, the two together are not
    coach = s.person(lambda f: (40.0, -0.7) if f < 105 else (37.0 - 0.04 * (f - 110), -0.7), DARK,
                     [f for f in range(40, 180) if not 100 <= f < 110])
    res, owner = s.link()
    mine = [i for i in res.identities if coach in people_in(i, owner)]
    assert all(people_in(i, owner) == {coach} for i in mine)
    assert all(i.role == OFF for i in mine)
    assert all(short not in people_in(i, owner) or people_in(i, owner) == {short} for i in res.identities)


def _one_tracklet(scene, with_kit=True):
    """Build, then hand the tracker's mistake back: the last two runs as one tracklet."""
    det, tracklets, owner = scene.build(with_kit)
    a, b = tracklets[-2], tracklets[-1]
    merged = Tracklet(a.tid, np.concatenate([a.rows, b.rows]), 'lost', 'new')
    return det, tracklets[:-2] + [merged], owner


def test_a_tracklet_that_changes_shirt_is_cut():
    s = Scene(120)
    background(s, range(120))
    w = walker(40.0, 20.0, vx=1.0)
    white = s.person(w, LIGHT, range(0, 60))
    black = s.person(w, DARK, range(60, 120))
    det, tracklets, owner = _one_tracklet(s)
    res = Linker(projector(s.n), PITCH_L, PITCH_W, LinkSettings()).link(tracklets, det)
    mine = [i for i in res.identities if people_in(i, owner) & {white, black}]
    assert len(mine) == 2
    assert all(len(people_in(i, owner)) == 1 for i in mine)


def test_a_dark_shirt_washed_grey_by_the_sun_is_not_cut():
    # nothing makes a black shirt read bright white; halfway to grey is still one person
    grey = dict(team=1, kit=[150.0, 128.0, 128.0, 50.0], desc=DARK['desc'])
    s = Scene(120)
    background(s, range(120))
    w = walker(40.0, 20.0, vx=1.0)
    s.person(w, DARK, range(0, 60))
    s.person(w, grey, range(60, 120))
    det, tracklets, owner = _one_tracklet(s)
    res = Linker(projector(s.n), PITCH_L, PITCH_W, LinkSettings()).link(tracklets, det)
    mine = [i for i in res.identities if 6 in people_in(i, owner)]
    assert len(mine) == 1 and (mine[0].rows >= 0).sum() == 120


def test_someone_walking_the_touchline_all_window_is_off():
    s = Scene(390)
    background(s, range(390))
    linesman = s.person(walker(10.0, 0.5, vx=1.0), DARK, range(0, 390))       # 13 s on the line
    winger = s.person(walker(10.0, PITCH_W - 0.5, vx=2.0), LIGHT, range(0, 150))  # 5 s on it
    res, owner = s.link()
    role = {next(iter(people_in(i, owner))): i.role for i in res.identities}
    assert role[linesman] == OFF
    assert role[winger] == PLAYER


def test_over_the_outfield_cap_the_least_sure_shirt_is_greyed_only_while_over():
    grey = dict(team=1, kit=[150.0, 128.0, 128.0, 50.0], desc=DARK['desc'])
    s = Scene(120)
    background(s, range(120))
    late = s.person(walker(35.0, 30.0), grey, range(60, 120))       # a fourth dark shirt, half the window
    res, owner = s.link(with_kit=True, outfield_cap=3)
    me = next(i for i in res.identities if people_in(i, owner) == {late})
    darks = [i for i in res.identities if i.team == me.team]
    assert len(darks) == 4 and all(i.role == PLAYER for i in darks)
    assert all(res.team_at(me, f) == UNKNOWN for f in range(60, 120))
    assert all(res.team_at(i, f) == me.team for i in darks if i is not me for f in range(120))
    assert not any(res.unsure.get(f) for f in range(60))
