"""Formation video: who plays where over a stretch of a half (2026-11-07).

    python -m pitchiq.experiments.formation_video <tag> --start 25:00 --secs 120 [--out PATH]

Top: the camera, each tracked player boxed with their person ID, and the top-down map with
the same IDs. Bottom: each team's formation, one card per position (10 outfield + keeper),
showing who the whole-half joiner (idlink) puts there: their picture (their tallest box of
the half that nobody else overlaps), their person ID (A7 = team A's 7th most-seen person)
and their shirt number when the reader is sure of it. A card is lit while its player is on
camera; otherwise it shows the last one seen there, dimmed, with how long ago.

Formation: each standard one (FORMATIONS) is fitted, with its own stretch of depth and width,
to the team's shape once a second where the whole-half map sees all 10 outfielders; the
lowest median error wins (`pick_formation`), or the one given with --formation B=4-4-2.
Others within 5% are shown as close. Positions are the moments averaged with players paired
to that formation. Cards are filled moment by moment, one player per position (`assign`):
players on camera are paired to positions by their recent place in the team's shape and
their half-average place, keep their card through tracking gaps and move only when the
pairing has put them elsewhere for 2 s; players seen too briefly for an ID get a card too
("no ID", live picture). The first version put each person on the position nearest their
half-average place, so split IDs piled up (wh2 team A: 7 people on DM) and two players on
camera could share one card while another stayed empty. Keeper: >= 30% of sightings. wb1: both teams a back four, the front six
close (A 4-1-4-1, close 4-2-3-1 and 4-3-3; B 4-3-3, close 4-1-4-1, 4-2-3-1 and 4-4-2; the
user saw 4-4-2 for Woodbridge). The first version clustered every sighting: 3-5-2, 4-1-4-1.

--lines (2026-11-08) joins each team's back, middle and front line on the map (team A cyan,
B orange): the formation fitted at each moment to the map's whole team (`fit_now`), each dot
in the line of its nearest map player. Against fitting the dots on camera alone, over seven
20 s clips: tighter lines (depth spread 3.0-4.8 m vs 2.9-5.3) and half the line changes per
player (0.04-0.64/s vs 0.13-1.17/s), but fewer dots on a line (62-86% vs 83-92%). Fitted
every frame the lines still looked like they were glitching, so who is in which line is
fitted once per LINE_HOLD = 3 s to each map player's average place over that stretch: no
changes inside a stretch, 0.01-0.09 per player per second at stretch ends, same dots on a line.

Roles come from idlink's `fix_roles`: touchline bystanders are dropped (not drawn), the
odd-kit person standing in a goal is its keeper, and one keeper piece per goal at a time.

Works for any half that has the whole-half map, `idlink frags` and `idlink numbers` (and
`idlink parts` for --parts). Held out halves (nb2, pi1, wh1, co1, wb2) need --final.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from pitchiq.experiments import idlink as L
from pitchiq.experiments.idslots import _kmeans

STEP = 3  # video frames per output frame (10 fps from 30)
CAM = (768, 432)
MAP_W = 512
PANEL = (640, 520)
SIZE = (CAM[0] + MAP_W, CAM[1] + PANEL[1])
PORT = (36, 64)  # portrait w, h
CARD = (80, 86)
HEAD = 24
NUM_P, NUM_W = (
    0.6,
    2.0,
)  # a number is shown when the pooled belief is this sure, on this much evidence
LETTER = 'AB'
# Cards are filled moment by moment, one player per position. Cost of a player at a position
# = squared distance from their recent place in the team's shape (EMA per output frame) +
# PRIOR_W x that from their half-average place. A player keeps their card: through tracking
# gaps up to BRIDGE output frames (lit for the first LIT_GAP), and until the best one-per-
# position pairing (Hungarian) has put them elsewhere for SWITCH frames in a row. Re-pairing
# every frame swapped cards ~150 times a minute per team, because players drop out of the
# samples for single frames and the pairing reshuffles around each gap.
EMA, PRIOR_W = 0.1, 0.5
BRIDGE, LIT_GAP, SWITCH = 15, 5, 20
CENTRE_S = 2.0  # the team centroid the places are measured from is averaged over this
KEEPER_SHARE = (
    0.3  # a person with this share of keeper sightings is a keeper (the linker misses some)
)
GREY, DARK, WHITE, BLACK, GOLD = (
    (150, 150, 150),
    (60, 60, 60),
    (255, 255, 255),
    (0, 0, 0),
    (0, 215, 255),
)
LINES_MIN = 8  # a team's lines are drawn when this many of its outfielders are on camera
MATCH_M = 4.0  # a dot takes the line of the map player within this many metres
LINE_HOLD = 3.0  # seconds: who is in which line is fitted once per stretch this long
LINE_COL = ((255, 220, 40), (30, 140, 255))  # map lines: team A cyan, B orange (BGR); kit
# colours often read alike on the map (co2: both near-black against the sunset)
FONT = cv2.FONT_HERSHEY_SIMPLEX
# Formations, back line first; each position (name, depth 0 back .. 1 front, width +1 team's left)
_BACK4 = [('LB', 0.05, 1.0), ('LCB', 0.0, 0.35), ('RCB', 0.0, -0.35), ('RB', 0.05, -1.0)]
_BACK3 = [('LCB', 0.0, 0.6), ('CB', 0.0, 0.0), ('RCB', 0.0, -0.6)]
_BACK5 = [
    ('LWB', 0.12, 1.0),
    ('LCB', 0.0, 0.5),
    ('CB', 0.0, 0.0),
    ('RCB', 0.0, -0.5),
    ('RWB', 0.12, -1.0),
]
_MID4 = [('LM', 0.5, 1.0), ('LCM', 0.45, 0.35), ('RCM', 0.45, -0.35), ('RM', 0.5, -1.0)]
_MID3 = [('LCM', 0.45, 0.5), ('CM', 0.4, 0.0), ('RCM', 0.45, -0.5)]
_TWO = [('LS', 1.0, 0.3), ('RS', 1.0, -0.3)]
_THREE = [('LW', 0.95, 0.85), ('ST', 1.0, 0.0), ('RW', 0.95, -0.85)]
_ONE = [('ST', 1.0, 0.0)]
FORMATIONS = {
    '4-4-2': [_BACK4, _MID4, _TWO],
    '4-3-3': [_BACK4, _MID3, _THREE],
    '4-2-3-1': [_BACK4, [('LDM', 0.3, 0.35), ('RDM', 0.3, -0.35)],
                [('LW', 0.7, 0.9), ('CAM', 0.7, 0.0), ('RW', 0.7, -0.9)], _ONE],
    '4-1-4-1': [_BACK4, [('DM', 0.25, 0.0)], [(n, 0.6, w) for n, _, w in _MID4], _ONE],
    '4-5-1': [_BACK4, [('LM', 0.5, 1.0), ('LCM', 0.45, 0.45), ('CM', 0.4, 0.0), ('RCM', 0.45, -0.45),
                       ('RM', 0.5, -1.0)], _ONE],
    '3-5-2': [_BACK3, [('LWB', 0.45, 1.0), ('LCM', 0.45, 0.4), ('CM', 0.4, 0.0), ('RCM', 0.45, -0.4),
                       ('RWB', 0.45, -1.0)], _TWO],
    '3-4-3': [_BACK3, _MID4, _THREE],
    '5-3-2': [_BACK5, _MID3, _TWO],
    '5-4-1': [_BACK5, _MID4, _ONE],
}  # fmt: skip


def _secs(s: str) -> float:
    v = 0.0
    for p in s.split(':'):
        v = v * 60 + float(p)
    return v


def _clock(s: float) -> str:
    s = int(round(s))
    return '%d:%02d' % (s // 60, s % 60)


def fit(C, template, iters=8):
    """Match 10 centres (metres, forward +x, team's left +y) to a formation's positions:
    alternately pair them (Hungarian) and fit depth = a + b * d, width = s * w by least
    squares. Returns (squared error in m^2, centre index per position in template order)."""
    from scipy.optimize import linear_sum_assignment

    T = np.array([(d, w) for line in template for _, d, w in line])
    a, b = C[:, 0].min(), np.ptp(C[:, 0]) or 1.0
    s = np.sort(np.abs(C[:, 1]))[-2] or 1.0
    for _ in range(iters):
        Q = np.stack([a + b * T[:, 0], s * T[:, 1]], 1)
        cost = ((Q[:, None] - C[None]) ** 2).sum(2)
        r, c = linear_sum_assignment(cost)
        A = np.stack([np.ones(len(r)), T[r, 0]], 1)
        a, b = np.linalg.lstsq(A, C[c, 0], rcond=None)[0]
        b = max(b, 1.0)
        s = max(float(T[r, 1] @ C[c, 1]) / max(float(T[r, 1] @ T[r, 1]), 1e-6), 1.0)
    Q = np.stack([a + b * T[:, 0], s * T[:, 1]], 1)
    cost = ((Q[:, None] - C[None]) ** 2).sum(2)
    r, c = linear_sum_assignment(cost)
    return float(cost[r, c].sum()), c[np.argsort(r)]


def fit_now(P, template, prev=None, stick=25.0, iters=6):
    """Each of n <= 10 players (metres from their centre, forward +x, team's left +y) put on
    one of the formation's positions at this moment, the formation stretched to them as in
    `fit`. `prev` (line index per player or -1) adds `stick` m^2 for leaving one's last line,
    so players between two lines don't flicker. Returns the line index per player."""
    from scipy.optimize import linear_sum_assignment

    T = np.array([(d, w) for line in template for _, d, w in line])
    line_of = np.array([li for li, line in enumerate(template) for _ in line])
    a, b = P[:, 0].min(), np.ptp(P[:, 0]) or 1.0
    s = np.sort(np.abs(P[:, 1]))[-2] if len(P) > 1 else 1.0
    s = s or 1.0
    pen = 0.0
    if prev is not None:
        pen = stick * ((prev[:, None] >= 0) & (prev[:, None] != line_of[None]))
    for _ in range(iters):
        Q = np.stack([a + b * T[:, 0], s * T[:, 1]], 1)
        r, c = linear_sum_assignment(((P[:, None] - Q[None]) ** 2).sum(2) + pen)
        A = np.stack([np.ones(len(r)), T[c, 0]], 1)
        if len(r) >= 3:
            a, b = np.linalg.lstsq(A, P[r, 0], rcond=None)[0]
            b = max(b, 1.0)
            s = max(float(T[c, 1] @ P[r, 1]) / max(float(T[c, 1] @ T[c, 1]), 1e-6), 1.0)
    out = np.full(len(P), -1)
    out[r] = line_of[c]
    return out


def team_moments(tag, tm, sign, step=30, min_moments=60):
    """The team's outfield shape once a second from the whole-half map (10 points, metres
    from the team's centre, forward +x, team's left +y), where all 10 outfielders are seen;
    moments with map guesses fill in when fewer than `min_moments` are. Returns (moments
    array (n, 10, 2), fully seen count) or None."""
    z = np.load(L.MAP / f'{tag}.npz')
    m = (z['team'] == tm) & (z['role'] == 0)
    f, x, y, s = z['f'][m], z['x'][m], z['y'][m], z['seen'][m]
    o = np.argsort(f, kind='stable')
    f, x, y, s = f[o], x[o], y[o], s[o]
    cut = np.flatnonzero(np.diff(f)) + 1
    full, guess = [], []
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(f)]):
        if b - a != 10 or int(f[a]) % step:
            continue
        P = sign * (np.stack([x[a:b], y[a:b]], 1) - [x[a:b].mean(), y[a:b].mean()])
        (full if s[a:b].all() else guess).append(P)
    shapes = full if len(full) >= min_moments else full + guess
    return (np.stack(shapes), len(full)) if len(shapes) >= 10 else None


def pick_formation(S, every=2):
    """Each standard formation fitted to every moment's shape (own stretch per moment); the
    one with the lowest median error wins. Returns {name: median error}.

    No starting point: averaging the moments first (pairing players to a running average)
    named a different formation depending on the moment it started from (wb1 team B: 4-3-3,
    4-2-3-1, 4-1-4-1 or 4-4-2 over 30 starts). Every start and this score agree on a back four
    for both wb1 teams; the front six are within a few percent of each other."""
    return {n: float(np.median([fit(P, t)[0] for P in S[::every]])) for n, t in FORMATIONS.items()}


def average_shape(S, template, iters=10):
    """The average of the moments with players paired (Hungarian) to the running average,
    started from the formation's own shape so the positions match its names."""
    from scipy.optimize import linear_sum_assignment

    T = np.array([(d, w) for line in template for _, d, w in line])
    ref = np.stack([(T[:, 0] - T[:, 0].mean()) * 25.0, T[:, 1] * 15.0], 1)
    for _ in range(iters):
        al = []
        for P in S:
            r, c = linear_sum_assignment(((ref[:, None] - P[None]) ** 2).sum(2))
            al.append(P[c])
        ref = np.mean(al, 0)
    return ref


def formation(P, w, k=10, seed=0, S=None, name=None):
    """Position centres in the team's shape (forward +x, team's left +y) and their names:
    from the map's moments `S` when given (formation by `pick_formation`, or `name`), else
    k-means over the sightings' places (the first version). Returns (centres, lines back to
    front as centre indices left to right, name per centre, formation name, error per
    formation)."""
    if S is not None:
        errs = pick_formation(S)
        best = name or min(errs, key=errs.get)
        C = average_shape(S, FORMATIONS[best])
        idx = iter(fit(C, FORMATIONS[best])[1])
    else:
        C = _kmeans(P, w, k, seed=seed)
        e = {n: fit(C, t) for n, t in FORMATIONS.items()}
        errs = {n: v[0] for n, v in e.items()}
        best = name or min(errs, key=errs.get)
        idx = iter(e[best][1])
    lines, names = [], {}
    for line in FORMATIONS[best]:
        g = []
        for nm, _, _ in line:
            j = int(next(idx))
            g.append(j)
            names[j] = nm
        lines.append(g)
    return C, lines, names, best, errs


def close_call(errs, best, share=0.05):
    """Other formations within `share` of the chosen one's error (the call is not clear)."""
    return [
        n
        for n, e in sorted(errs.items(), key=lambda t: t[1])
        if n != best and e <= errs[best] * (1 + share)
    ]


class Match:
    """The half's people (joined fragments), their positions, labels and pictures."""

    def __init__(self, tag, cues=('feat', 'place', 'num', 'last'), min_s=5.0, forms=None):
        self.forms = forms
        H = self.H = L.Half(tag)
        F = self.F = H.F
        self.tag, self.fps = tag, H.fps
        self.person = H.group(cues=cues, min_s=min_s)
        z = np.load(L.MAP / f'{tag}.npz')
        self.length, self.width = float(z['length_m']), float(z['width_m'])
        self.kit = {}
        for tm in (0, 1):
            lab = np.nanmedian(
                np.array([z['kit_lab'][w, tm ^ int(s)] for w, s in enumerate(z['swap'])]), 0
            )
            bgr = cv2.cvtColor(
                np.uint8([[np.clip(np.nan_to_num(lab, nan=128), 0, 255)]]), cv2.COLOR_LAB2BGR
            )[0, 0]
            self.kit[tm] = tuple(int(v) for v in bgr)
        self.cteam, self.sign = L.team_shape(tag, F)
        off = F['off']
        self._by_f = np.argsort(F['s_f'], kind='stable')
        self._sf_sorted = F['s_f'][self._by_f]
        self._frag = np.repeat(np.arange(len(off) - 1), np.diff(off))
        # people: team, seen frames, keeper share, average place, pooled number belief, best box
        P = {}
        for i in np.where(self.person >= 0)[0]:
            p = int(self.person[i])
            d = P.setdefault(p, dict(team=int(F['team'][i]), frags=[], seen=0.0, keep=0.0, place=np.zeros(2),
                                     pw=0.0, num=np.zeros(100), nw=0.0, best=(0.0, -1, None)))  # fmt: skip
            ns = float(F['n_seen'][i])
            d['frags'].append(int(i))
            d['seen'] += ns
            d['keep'] += ns * (F['role'][i] == 1)
            if F['role'][i] == 0 and np.isfinite(H.place[i]).all():
                d['place'] += H.place[i] * ns
                d['pw'] += ns
            if int(i) in H.numP:
                d['num'] += H.numP[int(i)][0]
                d['nw'] += H.numP[int(i)][1]
            b = F['s_box'][off[i] : off[i + 1]].astype(float)
            h, wd = b[:, 3] - b[:, 1], b[:, 2] - b[:, 0]
            ok = (h > 1.6 * wd) & (h < 4.0 * wd)
            for j in np.argsort(-h)[:12]:
                if ok[j] and h[j] > d['best'][0] and self._clear(off[i] + j):
                    d['best'] = (float(h[j]), int(F['s_f'][off[i] + j]), b[j])
                    break
        self.P = P
        # positions per team: the map's average team shape (k-means of sightings as fallback)
        self.form, self.moments = {}, {}
        for tm in (0, 1):
            ids = [
                i
                for i in np.where(self.person >= 0)[0]
                if F['team'][i] == tm and F['role'][i] == 0 and np.isfinite(H.place[i]).all()
            ]
            mo = team_moments(tag, tm, self.sign[tm])
            self.moments[tm] = (len(mo[0]), mo[1]) if mo else (0, 0)
            self.form[tm] = formation(
                H.place[ids],
                F['n_seen'][ids].astype(float),
                S=mo[0] if mo else None,
                name=(forms or {}).get(LETTER[tm]),
            )
        for tm in (0, 1):
            order = sorted((p for p in P if P[p]['team'] == tm), key=lambda p: -P[p]['seen'])
            for k, p in enumerate(order):
                d = P[p]
                d['id'] = f'{LETTER[tm]}{k + 1}'
                b = d['num'] / d['nw'] if d['nw'] > 0 else None
                d['number'] = (
                    int(b.argmax())
                    if b is not None and b.max() >= NUM_P and d['nw'] >= NUM_W
                    else None
                )
                d['label'] = d['id'] + (f' #{d["number"]}' if d['number'] is not None else '')
                if d['keep'] >= KEEPER_SHARE * d['seen']:
                    d['slot'] = 'GK'
                elif d['pw'] > 0:
                    C = self.form[tm][0]
                    d['slot'] = int(np.argmin(((C - d['place'] / d['pw']) ** 2).sum(1)))
                else:
                    d['slot'] = None

    def _clear(self, j, share=0.08):
        """Sample j's box is not overlapped by anyone else's at that frame (a clean picture)."""
        F = self.F
        o, so = self._by_f, self._sf_sorted
        # positions are kept every 3rd frame of each fragment, not on shared frames: look +-2
        lo, hi = (
            np.searchsorted(so, F['s_f'][j] - 2),
            np.searchsorted(so, F['s_f'][j] + 2, side='right'),
        )
        b = F['s_box'][j].astype(float)
        area = max((b[2] - b[0]) * (b[3] - b[1]), 1.0)
        for k in o[lo:hi]:
            if self._frag[k] == self._frag[j]:
                continue
            c = F['s_box'][k]
            iw = min(b[2], c[2]) - max(b[0], c[0])
            ih = min(b[3], c[3]) - max(b[1], c[1])
            if iw > 0 and ih > 0 and iw * ih / area > share:
                return False
        return True

    def samples(self, lo, hi):
        """{output frame: [(fragment, box, x, y)]}, each fragment's sample nearest the frame."""
        F = self.F
        frag = np.repeat(np.arange(len(F['off']) - 1), np.diff(F['off']))
        sf = F['s_f']
        out = {}
        for j in np.where((sf >= lo - 1) & (sf < hi + 2))[0]:
            t = lo + STEP * int(round((sf[j] - lo) / STEP))
            d = out.setdefault(t, {})
            i = int(frag[j])
            if i not in d or abs(sf[j] - t) < abs(d[i][0] - t):
                d[i] = (int(sf[j]), F['s_box'][j], float(F['s_x'][j]), float(F['s_y'][j]))
        return {t: [(i, v[1], v[2], v[3]) for i, v in d.items()] for t, d in out.items()}

    def portraits(self, cap, people):
        """Each person's tallest box of the half, cropped and fitted to PORT."""
        jobs = sorted((self.P[p]['best'][1], p) for p in people if self.P[p]['best'][1] >= 0)
        out = {}
        for f, p in jobs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, img = cap.read()
            if not ok:
                continue
            pic = portrait(img, self.P[p]['best'][2])
            if pic is not None:
                out[p] = pic
        return out

    def team_rows(self, f, tm):
        """The whole-half map's 10 outfielders of team tm at frame f (nearest frame within 2):
        (map slot per player, places (10, 2), seen on camera per player), or None. The map
        guesses players off camera, so a formation fitted to these has the whole team."""
        if not hasattr(self, '_rows'):
            z = np.load(L.MAP / f'{self.tag}.npz')
            m = (z['role'] == 0) & (z['slot'] >= 0)
            o = np.argsort(z['f'][m], kind='stable')
            self._rows = {k: z[k][m][o] for k in ('f', 'team', 'slot', 'x', 'y', 'seen')}
        R = self._rows
        for g in (f, f - 1, f + 1, f - 2, f + 2):
            a, b = np.searchsorted(R['f'], g), np.searchsorted(R['f'], g, side='right')
            m = R['team'][a:b] == tm
            if m.sum() >= 7:  # map slots can be empty for a while (wh2: 8-9 rows per team)
                xy = np.stack([R['x'][a:b][m], R['y'][a:b][m]], 1).astype(float)
                return R['slot'][a:b][m].astype(int), xy, R['seen'][a:b][m]
        return None

    def team_block(self, f0, f1, tm):
        """Each map slot's average place over frames [f0, f1) of team tm, measured from the
        team's centre at each frame (so a team moving up the pitch doesn't smear it): (slots,
        places (n, 2)) for slots seen in at least half the frames, or None for fewer than 7."""
        self.team_rows(f0, tm)  # loads the rows
        R = self._rows
        a, b = np.searchsorted(R['f'], f0), np.searchsorted(R['f'], f1)
        m = R['team'][a:b] == tm
        f, s = R['f'][a:b][m], R['slot'][a:b][m].astype(int)
        if not len(f):
            return None
        xy = np.stack([R['x'][a:b][m], R['y'][a:b][m]], 1).astype(float)
        fi = f - f0
        n = np.bincount(fi, minlength=f1 - f0)
        cen = np.stack([np.bincount(fi, xy[:, k], minlength=f1 - f0) for k in (0, 1)], 1)
        cen = cen / np.maximum(n, 1)[:, None]
        rel = xy - cen[fi]
        cnt = np.bincount(s)
        avg = np.stack([np.bincount(s, rel[:, k]) for k in (0, 1)], 1) / np.maximum(cnt, 1)[:, None]
        keep = np.flatnonzero(cnt >= 0.5 * (n > 0).sum())
        return (keep, avg[keep]) if len(keep) >= 7 else None

    def centre(self, f, tm):
        """The team's outfield centroid at frame f (nearest frame where the map has it):
        every outfield row of the map, its guesses for players off camera included, averaged
        over CENTRE_S. The seen-only centroid jumps metres whenever a player walks into or out
        of the picture, and every card near a boundary flipped with it."""
        if not hasattr(self, '_cmap'):
            z = np.load(L.MAP / f'{self.tag}.npz')
            n = len(self.cteam)
            k = np.ones(int(round(CENTRE_S * self.fps)) | 1)
            self._cmap = np.full((n, 2, 2), np.nan)
            for t in (0, 1):
                m = (z['team'] == t) & (z['role'] == 0) & (z['f'] < n)
                cnt = np.convolve(np.bincount(z['f'][m], minlength=n), k, 'same')
                for a, key in enumerate(('x', 'y')):
                    s = np.convolve(np.bincount(z['f'][m], z[key][m], minlength=n), k, 'same')
                    with np.errstate(invalid='ignore', divide='ignore'):
                        self._cmap[:, t, a] = np.where(cnt >= 6 * len(k) // 2, s / cnt, np.nan)
        C = self._cmap
        for d in range(0, 600, 3):
            for g in (f - d, f + d):
                if 0 <= g < len(C) and np.isfinite(C[g, tm]).all():
                    return C[g, tm]
        return np.nanmean(C[:, tm], 0)


def portrait(img, box):
    """The box cropped (10% headroom) and fitted to PORT, or None."""
    x1, y1, x2, y2 = (float(v) for v in box)
    h = y2 - y1
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    hh = h * 1.1 / 2
    hw = hh * PORT[0] / PORT[1]
    r = [
        int(max(0, cx - hw)),
        int(max(0, cy - hh)),
        int(min(img.shape[1], cx + hw)),
        int(min(img.shape[0], cy + hh)),
    ]
    crop = img[r[1] : r[3], r[0] : r[2]]
    return cv2.resize(crop, PORT, interpolation=cv2.INTER_AREA) if crop.size else None


def _text(img, s, org, scale=0.4, col=WHITE, th=1, bg=None):
    (w, h), base = cv2.getTextSize(s, FONT, scale, th)
    x, y = int(org[0]), int(org[1])
    if bg is not None:
        cv2.rectangle(img, (x - 2, y - h - 3), (x + w + 2, y + base), bg, -1)
    cv2.putText(img, s, (x, y), FONT, scale, col, th, cv2.LINE_AA)
    return w


def _ink(bgr):
    """Black or white text, whichever reads on this fill."""
    return BLACK if 0.114 * bgr[0] + 0.587 * bgr[1] + 0.299 * bgr[2] > 140 else WHITE


class Video:
    def __init__(self, M: Match, lo, hi, out: Path, crf=36, lines=False):
        from pitchiq.experiments.idlab import _Clip

        self.M, self.lo, self.hi, self.out = M, lo, hi, out
        self.lines = lines  # draw each team's lines (back, middle, front) on the map
        self.pos = {}  # entry -> its latest map place (x, y)
        self.present = {0: [], 1: []}  # entries on camera at this frame
        self.line_of = {}  # (team, map slot) -> the formation line it was in last
        self.blocks = {}  # (team, stretch) -> ({map slot: line}, {entry: line})
        self.writer = _Clip(out, SIZE, fps=int(round(M.fps / STEP)), crf=crf)
        self.vis = M.samples(lo, hi)
        F, P = M.F, M.P
        # last time each person was seen before the stretch (for dimmed cards at the start)
        self.last = {}
        for p, d in P.items():
            before = [min(int(F['last'][i]), lo) for i in d['frags'] if F['first'][i] <= lo]
            if before:
                self.last[p] = max(before)
        self.during = {
            int(M.person[i]) for v in self.vis.values() for i, *_ in v if M.person[i] >= 0
        }
        self.px = min((MAP_W - 24) / M.length, (CAM[1] - 70) / M.width)
        # per-moment positions: an entry is ('p', person) or ('f', fragment) for a player
        # seen too briefly to have an ID
        self.ema = {}
        self.hold = {0: {}, 1: {}}  # entry -> the position whose card it holds
        self.gap, self.bad = {}, {}  # entry -> frames unseen / frames placed elsewhere
        self.held = {0: {}, 1: {}}  # position -> (entry, frame) it was last lit
        self.crop = {}  # entry without an ID -> its latest live picture

    def run(self):
        import imageio_ffmpeg  # noqa: F401  (the writer's encoder; fail before the long read)

        from pitchiq.experiments.wholemap import video_of

        cap = cv2.VideoCapture(video_of(self.M.tag))
        want = set(self.during) | {p for p in self.M.P if self.M.P[p]['slot'] is not None}
        self.port = self.M.portraits(cap, want)
        cap.set(cv2.CAP_PROP_POS_FRAMES, self.lo)
        f = self.lo
        while f < self.hi:
            ok, img = cap.read()
            if not ok:
                break
            if (f - self.lo) % STEP == 0:
                self.writer.write(self.frame(f, img))
            f += 1
        self.writer.release()

    # ---- one output frame
    def frame(self, f, img):
        M = self.M
        now = self.vis.get(f, [])
        on = {}
        for i, b, x, y in now:
            p = int(M.person[i])
            if p >= 0:
                on.setdefault(p, []).append((b, x, y))
                self.last[p] = f
        cur = {tm: self.assign(tm, f, now, img) for tm in (0, 1)}
        self._f = f
        canvas = np.full((SIZE[1], SIZE[0], 3), 24, np.uint8)
        canvas[: CAM[1], : CAM[0]] = self.camera(f, img, now)
        canvas[: CAM[1], CAM[0] :] = self.pitch(now, cur)
        for tm in (0, 1):
            canvas[CAM[1] :, tm * PANEL[0] : (tm + 1) * PANEL[0]] = self.panel(tm, f, on, cur[tm])
        return canvas

    def assign(self, tm, f, now, img):
        """{position: entry} lit at frame f: one player per position, each keeping their
        card through short gaps and until the best pairing has moved them for SWITCH frames."""
        from scipy.optimize import linear_sum_assignment

        M, F = self.M, self.M.F
        C = M.form[tm][0]
        ent = {}
        for i, b, x, y in now:
            if int(F['team'][i]) != tm or int(F['role'][i]) != 0:
                continue
            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            p = int(M.person[i])
            if p >= 0 and M.P[p]['slot'] == 'GK':
                continue
            ent.setdefault(('p', p) if p >= 0 else ('f', int(i)), []).append((b, x, y))
        hold, gap, bad = self.hold[tm], self.gap, self.bad
        self.present[tm] = list(ent)
        c = M.centre(f, tm)
        for k, v in ent.items():
            self.pos[k] = np.mean([(x, y) for _, x, y in v], 0)
            rel = M.sign[tm] * (self.pos[k] - c)
            e = self.ema.get(k)
            self.ema[k] = rel if e is None else e + EMA * (rel - e)
            gap[k] = 0
            if k[0] == 'f':
                pic = portrait(img, v[0][0])
                if pic is not None:
                    self.crop[k] = pic
        for k in list(hold):
            if k not in ent:
                gap[k] = gap.get(k, 0) + 1
                if gap[k] > BRIDGE:
                    del hold[k]
                    bad.pop(k, None)
        keys = list(dict.fromkeys(list(ent) + list(hold)))
        if not keys:
            return {}
        cost = np.zeros((len(keys), len(C)))
        for r, k in enumerate(keys):
            cost[r] = ((C - self.ema[k]) ** 2).sum(1)
            if k[0] == 'p' and M.P[k[1]]['pw'] > 0:
                home = M.P[k[1]]['place'] / M.P[k[1]]['pw']
                cost[r] += PRIOR_W * ((C - home) ** 2).sum(1)
        rows, cols = linear_sum_assignment(cost)
        ideal = {keys[r]: int(s) for r, s in zip(rows, cols)}
        # holders the best pairing has put elsewhere for SWITCH frames move there, when that
        # card is free or its holder is moving too
        movers = []
        for k, s in hold.items():
            bad[k] = bad.get(k, 0) + 1 if ideal.get(k, s) != s else 0
            if bad[k] >= SWITCH:
                movers.append(k)
        for k in movers:
            del hold[k]
        for k in movers:
            if k in ideal and ideal[k] not in hold.values():
                hold[k] = ideal[k]
                bad[k] = 0
        # new players take free cards (Hungarian); players gone longest give theirs up first
        new = [k for k in ent if k not in hold]
        free = [s for s in range(len(C)) if s not in hold.values()]
        for k in sorted((k for k in hold if gap.get(k, 0) > 0), key=lambda k: -gap[k]):
            if len(free) >= len(new):
                break
            free.append(hold.pop(k))
        if new and free:
            sub = cost[np.ix_([keys.index(k) for k in new], free)]
            for r, s in zip(*linear_sum_assignment(sub)):
                hold[new[r]] = free[s]
                bad[new[r]] = 0
        out = {s: k for k, s in hold.items() if gap.get(k, 0) <= LIT_GAP}
        for s, k in out.items():
            self.held[tm][s] = (k, f)
        return out

    def _style(self, i):
        """Colour, label and line width of fragment i's box / dot."""
        M, F = self.M, self.M.F
        role, team, p = int(F['role'][i]), int(F['team'][i]), int(M.person[i])
        if role >= 2:
            return DARK, None
        col = M.kit[team] if team < 2 else GREY
        lab = M.P[p]['label'] if p >= 0 else None
        if role == 1 or (p >= 0 and M.P[p]['slot'] == 'GK'):
            col = GOLD
        return col, lab

    def camera(self, f, img, now):
        s = CAM[0] / img.shape[1]
        cam = cv2.resize(img, CAM, interpolation=cv2.INTER_AREA)
        for i, b, _, _ in now:
            if self.M.F['role'][i] == 3:
                continue
            col, lab = self._style(i)
            x1, y1, x2, y2 = (int(v * s) for v in b)
            cv2.rectangle(cam, (x1, y1), (x2, y2), col, 2 if lab else 1)
            if lab:
                _text(cam, lab, (x1, y1 - 3), 0.38, _ink(col), 1, bg=col)
        t = f / self.M.fps
        _text(cam, f'{self.M.tag}  video {_clock(t)}   ({_clock((f - self.lo) / self.M.fps)} of {_clock((self.hi - self.lo) / self.M.fps)})',
              (8, CAM[1] - 10), 0.5, WHITE, 1, bg=BLACK)  # fmt: skip
        return cam

    def pitch(self, now, cur=None):
        M, px = self.M, self.px
        im = np.full((CAM[1], MAP_W, 3), (40, 105, 40), np.uint8)
        ox = (MAP_W - M.length * px) / 2
        oy = 34

        def p(x, y):
            return int(ox + x * px), int(oy + (M.width - y) * px)

        line = (225, 225, 225)
        L_, W_ = M.length, M.width
        cv2.rectangle(im, p(0, 0), p(L_, W_), line, 1)
        cv2.line(im, p(L_ / 2, 0), p(L_ / 2, W_), line, 1)
        cv2.circle(im, p(L_ / 2, W_ / 2), int(9.15 * px), line, 1, cv2.LINE_AA)
        for x0, d in ((0, 1), (L_, -1)):
            for depth, wide in ((16.5, 40.32), (5.5, 18.32)):
                cv2.rectangle(
                    im, p(x0, (W_ - wide) / 2), p(x0 + d * depth, (W_ + wide) / 2), line, 1
                )
        for tm in (0, 1):
            arrow = '->' if M.sign[tm] > 0 else '<-'
            x = 8 + tm * MAP_W // 2
            cv2.rectangle(im, (x, 8), (x + 14, 22), M.kit[tm], -1)
            cv2.rectangle(im, (x, 8), (x + 14, 22), WHITE, 1)
            _text(im, f'team {LETTER[tm]} attacks {arrow}', (x + 20, 20), 0.42)
        if self.lines:
            # each line of the formation (back, middle, front) joins the players in it,
            # side to side. Who is in which line is decided once per LINE_HOLD seconds: the
            # formation fitted to each map player's average place over that stretch (the
            # map's whole team, its guesses for players off camera included; fitting only
            # the players on camera put players in the wrong line whenever one or two were
            # missing). Fitted every frame, players jumped between lines 0.04-0.64 times a
            # second and the lines looked like they were glitching. Not the cards: they
            # follow each player's usual place, so lines drawn through them zigzagged across
            # the pitch. Each dot on camera takes the line of the map player nearest it the
            # first time it is seen in the stretch and keeps it to the stretch's end.
            hold = int(round(LINE_HOLD * M.fps))
            b = (self._f - self.lo) // hold
            f0 = self.lo + b * hold
            for tm in (0, 1):
                key = (tm, b)
                if key not in self.blocks:
                    blk = M.team_block(f0, min(f0 + hold, self.hi), tm)
                    li = {}
                    if blk is not None:
                        slots, A = blk
                        prev = np.array([self.line_of.get((tm, s), -1) for s in slots])
                        fit = fit_now(M.sign[tm] * A, FORMATIONS[M.form[tm][3]], prev)
                        li = {int(s): int(v) for s, v in zip(slots, fit) if v >= 0}
                        for s, v in li.items():
                            self.line_of[(tm, s)] = v
                    self.blocks[key] = (li, {})
                li, ent_line = self.blocks[key]
                ks = self.present[tm]
                if not li or len(ks) < LINES_MIN:
                    continue
                rows = M.team_rows(self._f, tm)
                if rows is not None:
                    slots, X, _ = rows
                    for k in ks:
                        if k in ent_line:
                            continue
                        d = np.linalg.norm(X - self.pos[k], axis=1)
                        j = int(d.argmin())
                        if d[j] < MATCH_M and int(slots[j]) in li:
                            ent_line[k] = li[int(slots[j])]
                for g in range(len(FORMATIONS[M.form[tm][3]])):
                    vs = [self.pos[k] for k in ks if ent_line.get(k) == g]
                    if len(vs) < 2:
                        continue
                    vs.sort(key=lambda q: q[1])
                    pts = np.array([p(*q) for q in vs], np.int32)
                    cv2.polylines(im, [pts], False, BLACK, 5, cv2.LINE_AA)
                    cv2.polylines(im, [pts], False, LINE_COL[tm], 2, cv2.LINE_AA)
        for i, _, x, y in now:
            if not (np.isfinite(x) and np.isfinite(y)) or self.M.F['role'][i] == 3:
                continue
            col, lab = self._style(i)
            c = p(x, y)
            cv2.circle(im, c, 6, col, -1, cv2.LINE_AA)
            cv2.circle(im, c, 6, BLACK, 1, cv2.LINE_AA)
            if lab:
                _text(im, lab.split(' ')[0], (c[0] + 7, c[1] - 4), 0.36, WHITE, 1)
        _text(
            im,
            'lines: back, middle, front line  (team A cyan, team B orange)'
            if self.lines
            else 'dots: players on camera now; label = person ID',
            (8, CAM[1] - 10),
            0.4,
            (210, 210, 210),
        )
        return im

    def panel(self, tm, f, on, cur):
        M = self.M
        C, lines, names, form, errs = M.form[tm]
        given = (M.forms or {}).get(LETTER[tm]) == form
        close = close_call(errs, form)
        shown = form + (' (given)' if given else f' (close: {close[0]})' if close else '')
        im = np.full((PANEL[1], PANEL[0], 3), (30, 52, 30), np.uint8)
        cv2.rectangle(im, (0, 0), (PANEL[0] - 1, PANEL[1] - 1), (70, 90, 70), 1)
        cv2.rectangle(im, (8, 6), (22, 20), M.kit[tm], -1)
        cv2.rectangle(im, (8, 6), (22, 20), WHITE, 1)
        n_on = len(cur)  # outfield players on a lit card, the ones without an ID included
        n_all = sum(1 for d in M.P.values() if d['team'] == tm)
        _text(
            im,
            f'team {LETTER[tm]}   {shown}   on camera {n_on}   people this half {n_all}',
            (30, 18),
            0.45,
        )
        rows = list(reversed([[j for j in g] for g in lines])) + [['GK']]
        rh = (PANEL[1] - HEAD) / len(rows)
        self.card_size = (CARD[0], int(min(CARD[1], rh - 6)))
        by_slot = {}
        for p, d in M.P.items():
            if d['team'] == tm and d['slot'] is not None:
                by_slot.setdefault(d['slot'], []).append(p)
        lit_now = set(cur.values())
        for r, row in enumerate(rows):
            cy = HEAD + rh * (r + 0.5)
            for k, s in enumerate(row):
                cx = PANEL[0] * (k + 1) / (len(row) + 1)
                if s != 'GK' and s in cur:
                    show, lit, note = [cur[s]], True, ''
                elif s != 'GK' and s in self.held[tm] and self.held[tm][s][0] not in lit_now:
                    e, fh = self.held[tm][s]
                    show, lit, note = [e], False, f'{(f - fh) / M.fps:.0f}s ago'
                else:
                    # GK, or a position nobody has held yet in this clip: the people whose
                    # half-average place is nearest it (and are not lit on another card)
                    occ = [p for p in by_slot.get(s, []) if ('p', p) not in lit_now]
                    show, lit, note = self._usual(occ, on, f)
                self.card(
                    im, tm, s, 'GK' if s == 'GK' else names[s], show, lit, note, int(cx), int(cy)
                )
        return im

    def _usual(self, occ, on, f):
        """(entries, lit, note) from the people whose usual position this is."""
        M = self.M
        live = sorted((p for p in occ if p in on), key=lambda p: -M.P[p]['seen'])[:1]
        if live:
            return [('p', p) for p in live], True, ''
        seen = [p for p in occ if p in self.last and self.last[p] <= f]
        if seen:
            p = max(seen, key=lambda q: self.last[q])
            return [('p', p)], False, f'{(f - self.last[p]) / M.fps:.0f}s ago'
        if occ:
            return [('p', max(occ, key=lambda q: M.P[q]['seen']))], False, 'not yet'
        return [], False, 'nobody'

    def card(self, im, tm, slot, name, show, lit, note, cx, cy):
        M = self.M
        w, h = self.card_size
        x0, y0 = cx - w // 2, cy - h // 2
        col = M.kit[tm] if slot != 'GK' else GOLD
        cv2.rectangle(im, (x0, y0), (x0 + w, y0 + h), (20, 30, 20), -1)
        cv2.rectangle(im, (x0, y0), (x0 + w, y0 + h), col if lit else DARK, 3 if lit else 1)
        _text(im, name, (x0 + 4, y0 + 12), 0.38, WHITE if lit else GREY)
        if note:
            _text(
                im,
                note,
                (x0 + w - 4 - cv2.getTextSize(note, FONT, 0.3, 1)[0][0], y0 + 12),
                0.3,
                GREY,
            )
        n = len(show)
        for k, (kind, v) in enumerate(show):
            px0 = x0 + (w - n * PORT[0] - (n - 1) * 4) // 2 + k * (PORT[0] + 4)
            py0 = y0 + 16
            pic = self.port.get(v) if kind == 'p' else self.crop.get((kind, v))
            if pic is not None:
                im[py0 : py0 + PORT[1], px0 : px0 + PORT[0]] = (
                    pic if lit else (pic * 0.45).astype(np.uint8)
                )
            else:
                cv2.rectangle(im, (px0, py0), (px0 + PORT[0], py0 + PORT[1]), DARK, 1)
            if kind == 'f':
                _text(im, 'no ID', (px0, y0 + h - 4), 0.33, WHITE if lit else GREY)
                continue
            d = M.P[v]
            _text(im, d['id'], (px0, y0 + h - 4), 0.33, WHITE if lit else GREY)
            if d['number'] is not None:
                _text(im, f'#{d["number"]}', (px0 + 1, py0 + PORT[1] - 3), 0.33, WHITE, 1, bg=BLACK)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('tag')
    ap.add_argument('--start', default=None, help='video time m:ss (default: 10 min into the half)')
    ap.add_argument('--secs', type=float, default=120.0)
    ap.add_argument('--out', default=None)
    ap.add_argument(
        '--parts', action='store_true', help="add the body-part look cue ('idlink parts' first)"
    )
    ap.add_argument('--crf', type=int, default=36)
    ap.add_argument(
        '--lines', action='store_true', help="draw each team's back, middle and front lines on the map"
    )
    ap.add_argument('--final', action='store_true')
    ap.add_argument(
        '--formation',
        default=None,
        help="a team's known formation, e.g. B=4-4-2 or A=4-3-3,B=4-4-2 (else picked from the map)",
    )
    a = ap.parse_args(argv)
    L._guard(a.tag, a.final)
    cues = ('feat', 'place', 'num', 'last') + (('parts',) if a.parts else ())
    forms = dict(kv.split('=') for kv in a.formation.split(',')) if a.formation else None
    for v in (forms or {}).values():
        if v not in FORMATIONS:
            raise SystemExit(f'unknown formation {v}: one of {", ".join(FORMATIONS)}')
    M = Match(a.tag, cues=cues, forms=forms)
    lo = int(_secs(a.start) * M.fps) if a.start else int(M.F['win_lo'][0] + 600 * M.fps)
    hi = lo + int(a.secs * M.fps)
    out = Path(a.out) if a.out else L.OUT / f'formation_{a.tag}_{lo}.webm'
    print(
        'roles fixed (pieces):',
        M.H.fixed,
        '  people:',
        {LETTER[t]: sum(1 for d in M.P.values() if d['team'] == t) for t in (0, 1)},
    )
    for tm in (0, 1):
        C, lines, names, form, errs = M.form[tm]
        print(
            f'team {LETTER[tm]}: {form}  ({M.moments[tm][0]} moments, {M.moments[tm][1]} fully seen)  '
            + '  '.join(f'{names[j]}({C[j, 0]:+.0f},{C[j, 1]:+.0f})' for g in lines for j in g)
        )
        print(
            '   fit error m^2: '
            + '  '.join(f'{n} {e:.0f}' for n, e in sorted(errs.items(), key=lambda t: t[1]))
            + (f'   close: {" ".join(close_call(errs, form))}' if close_call(errs, form) else '')
        )
    Video(M, lo, hi, out, crf=a.crf, lines=a.lines).run()
    print(
        f'{_clock(lo / M.fps)}..{_clock(hi / M.fps)} -> {out} ({out.stat().st_size / 1e6:.1f} MB)'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
