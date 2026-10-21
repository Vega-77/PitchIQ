"""Top-down map: everyone on the pitch, every frame, smooth, honest about who is unseen.

`build` takes the linker's identities and returns a `TopDown`: where each person
stands, in metres, for every frame of the window. Three steps:

* `feet`: each box's bottom centre through the frame's homography, weighted by
  how far a pixel reaches there and down-weighted when the box is the wrong
  height for a person (half hidden, or two people).
* `chain` / `visits`: per team ten outfield slots, a keeper and a referee slot;
  identities are chained into slots by min-cost flow, one visit at a time.
* `smooth`: a robust Rauch-Tung-Striebel smoother per slot, with velocity
  reverting towards the team's, so an unseen player moves with the team.
  `ghost_alpha` fades a guess by its spread, and hides it where the picture
  contradicts it.

Positions are metres on the pitch (x along, y across, 0..length, 0..width).

Design notes, measurements and history: `docs/design/topdown.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .linking import KEEPER, OFF, PLAYER, REFEREE, LinkResult
from .pitchtrack import FOOT_SIGMA_FLOOR_PX, FOOT_SIGMA_H, FOOT_SIGMA_W, PitchProjector


@dataclass
class MapSettings:
    fps: float = 30.0
    # feet
    foot_sigma_w: float = FOOT_SIGMA_W  # pixel noise across, share of box width
    foot_sigma_h: float = FOOT_SIGMA_H  # pixel noise down, share of box height
    foot_sigma_floor_px: float = FOOT_SIGMA_FLOOR_PX
    height_tol: float = 0.15  # |log(box / person height)| still a clean box
    height_scale: float = 0.10  # beyond that, noise variance x (1 + 10 k^2), k in these
    # smoother
    accel: float = 4.0  # white-noise acceleration density, m^2/s^3
    tau_s: float = 1.0  # an unseen run keeps its speed for about this long
    team_drift: float = 0.75  # ...then moves with this share of its team's seen players
    drift_s: float = 0.5  # (their mean velocity, averaged over +-this)
    huber: float = 2.5  # residuals beyond this many sigmas count less
    iters: int = 3
    # slots
    outfield: int = 10
    start_cost: float = 6.0  # a slot that begins (or ends) mid-window
    bridge_sigma_m: float = 1.5
    diffusion: float = 20.0  # m^2/s: how far an unseen player wanders
    max_speed: float = 8.0  # m/s, average over a bridge
    view_cost: float = 1.0  # per second a bridge spends in the picture
    value: float = 30.0  # for putting an identity in a slot at all
    value_per_s: float = 10.0
    jump_cost: float = 20.0  # a slot handing over with no plausible run between
    # the camera's view
    view_grace_s: float = 1.0  # unseen this long in view is still a detector miss
    view_margin_m: float = 1.5
    blink_s: float = 1.0  # an identity unseen this long is still drawn as seen
    shadow_m: float = 2.0  # a guess this close to a seen teammate is that teammate
    fade_s: float = 0.3
    pitch_margin_m: float = 3.0


@dataclass
class Dot:
    x: float  # metres
    y: float
    seen: bool  # a box (or the linker's short fill) this frame
    alpha: float  # 1 when seen
    group: tuple  # (team, role): which colour
    ident: object = None  # the Identity while seen
    slot: int = -1  # -1 for an identity outside the slots
    team: str | None = None  # the team it is drawn as ('unknown': greyed by the cap)

    @property
    def drawn_team(self) -> str:
        return self.team if self.team is not None else self.group[0]


@dataclass
class TopDown:
    lo: int
    hi: int
    dots: list  # per frame (lo..hi-1): [Dot]
    slots: list = field(default_factory=list)  # (group, [(pid, visit)])
    extras: list = field(default_factory=list)  # (pid, visit) outside every slot
    path: np.ndarray | None = None  # (tracks, frames, 2) metres, every frame
    owner: np.ndarray | None = None  # (tracks, frames) pid seen there, or -1
    alpha: np.ndarray | None = None  # (tracks, frames) strength of the guess while unseen
    slot_of: np.ndarray | None = None  # per track: its slot, -1 for an extra
    group: list | None = None  # per track: (team, role) it is drawn as

    def at(self, frame: int) -> list:
        return self.dots[frame - self.lo]


# ---- feet ----------------------------------------------------------------------------


def feet(res: LinkResult, projector: PitchProjector, s: MapSettings, person_px=None) -> dict:
    """pid -> (frames, (n, 2) metres, (n, 2, 2) covariances), detected rows only.

    `person_px(frames, boxes)`, when given, is the height in pixels of a 1.75 m
    person standing at each box's feet.
    """
    out = {}
    for ident in res.identities:
        if ident.role == OFF:
            continue
        keep = ~ident.interp & np.array([projector.has(f) for f in ident.frames], dtype=bool)
        if not keep.any():
            continue
        fs, b = ident.frames[keep], ident.boxes[keep].astype(np.float64)
        u, v = (b[:, 0] + b[:, 2]) / 2, b[:, 3]
        H = np.stack([projector.H[int(f)] for f in fs])
        q = np.einsum('nij,nj->ni', H, np.c_[u, v, np.ones(len(u))])
        xy = q[:, :2] / q[:, 2:3]
        w = q[:, 2]
        J = np.empty((len(fs), 2, 2))
        for r in range(2):
            J[:, r, 0] = (H[:, r, 0] - xy[:, r] * H[:, 2, 0]) / w
            J[:, r, 1] = (H[:, r, 1] - xy[:, r] * H[:, 2, 1]) / w
        su = s.foot_sigma_w * (b[:, 2] - b[:, 0]) + s.foot_sigma_floor_px
        sv = s.foot_sigma_h * (b[:, 3] - b[:, 1]) + s.foot_sigma_floor_px
        R = np.einsum('nij,nj,nkj->nik', J, np.c_[su * su, sv * sv], J)
        if person_px is not None:
            k = np.abs(np.log((b[:, 3] - b[:, 1]) / np.maximum(person_px(fs, b), 1.0)))
            k = np.maximum(k - s.height_tol, 0.0) / s.height_scale
            R *= (1.0 + 10.0 * k * k)[:, None, None]
        out[ident.pid] = (fs, xy, R)
    return out


# ---- the smoother --------------------------------------------------------------------


def _model(s: MapSettings):
    dt = 1.0 / s.fps
    phi = np.exp(-dt / s.tau_s)
    a = s.tau_s * (1.0 - phi)
    F = np.array([[1, 0, a, 0], [0, 1, 0, a], [0, 0, phi, 0], [0, 0, 0, phi]], dtype=np.float64)
    q = s.accel
    Q = q * np.array(
        [
            [dt**3 / 3, 0, dt**2 / 2, 0],
            [0, dt**3 / 3, 0, dt**2 / 2],
            [dt**2 / 2, 0, dt, 0],
            [0, dt**2 / 2, 0, dt],
        ]
    )
    return F, Q


def smooth(z: np.ndarray, R: np.ndarray, mask: np.ndarray, s: MapSettings, drift=None):
    """Robust RTS smoother over N tracks on one timeline.

    z (N, T, 2) metres, R (N, T, 2, 2), mask (N, T): which entries are
    measurements. Returns states (N, T, 4) = x, y, vx, vy and covariances
    (N, T, 4, 4). Every frame gets a state; frames without a measurement are
    the model's interpolation (or its glide, at the ends). Velocity reverts
    to `drift` (N, T, 2) m/s where given, else to standing still.
    """
    N, T = mask.shape
    F, Q = _model(s)
    dt = 1.0 / s.fps
    a = F[0, 2]
    phi = F[2, 2]
    z = np.where(mask[..., None], z, 0.0)
    eye2 = np.broadcast_to(np.eye(2), R.shape)
    R = np.where(mask[..., None, None], R, eye2)
    w = np.ones((N, T))
    v0 = s.accel * s.tau_s / 2.0  # stationary velocity variance
    for _ in range(max(1, s.iters)):
        xf = np.empty((N, T, 4))
        Pf = np.empty((N, T, 4, 4))
        xp = np.empty((N, T, 4))
        Pp = np.empty((N, T, 4, 4))
        x = np.zeros((N, 4))
        P = np.broadcast_to(np.diag([1e6, 1e6, v0, v0]), (N, 4, 4)).copy()
        for t in range(T):
            if t:
                x = x @ F.T
                if drift is not None:
                    d = drift[:, t - 1]
                    x = x + np.c_[(dt - a) * d, (1.0 - phi) * d]
                P = F @ P @ F.T + Q
            xp[:, t], Pp[:, t] = x, P
            m = mask[:, t]
            if m.any():
                S = P[m, :2, :2] + R[m, t] / w[m, t, None, None]
                K = P[m, :, :2] @ np.linalg.inv(S)
                r = z[m, t] - x[m, :2]
                x = x.copy()
                P = P.copy()
                x[m] = x[m] + np.einsum('nij,nj->ni', K, r)
                P[m] = P[m] - K @ P[m, :2, :]
            xf[:, t], Pf[:, t] = x, P
        xs = xf.copy()
        Ps = Pf.copy()
        for t in range(T - 2, -1, -1):
            C = Pf[:, t] @ F.T @ np.linalg.inv(Pp[:, t + 1])
            xs[:, t] = xf[:, t] + np.einsum('nij,nj->ni', C, xs[:, t + 1] - xp[:, t + 1])
            Ps[:, t] = Pf[:, t] + C @ (Ps[:, t + 1] - Pp[:, t + 1]) @ np.swapaxes(C, 1, 2)
        r = z - xs[..., :2]
        d2 = np.einsum('nti,ntij,ntj->nt', r, np.linalg.inv(R), r)
        w = np.where(mask, np.minimum(1.0, s.huber / np.sqrt(np.maximum(d2, 1e-12))), 1.0)
    return xs, Ps


def _timeline(tracks, lo: int, T: int):
    """Stack per-track (frames, xy, R) onto one window timeline."""
    N = len(tracks)
    z = np.zeros((N, T, 2))
    R = np.broadcast_to(np.eye(2), (N, T, 2, 2)).copy()
    mask = np.zeros((N, T), dtype=bool)
    for n, (fs, xy, cov) in enumerate(tracks):
        k = np.asarray(fs) - lo
        ok = (k >= 0) & (k < T)
        z[n, k[ok]], R[n, k[ok]], mask[n, k[ok]] = xy[ok], cov[ok], True
    return z, R, mask


# ---- the camera's view ---------------------------------------------------------------


def view_polygon(
    projector: PitchProjector, frame: int, length_m: float, width_m: float, step_px: int = 20
):
    """The ground the picture shows, in metres: up each side edge as far as it stays near the pitch."""
    W, Hh = projector.width, projector.height
    h = projector.H[int(frame)]
    sgn = np.sign((h @ [W / 2, Hh - 1, 1.0])[2])

    def top(u):
        best = Hh - 1
        for v in range(Hh - 1, -1, -step_px):
            q = h @ [u, v, 1.0]
            if np.sign(q[2]) != sgn:
                break
            x, y = q[:2] / q[2]
            if not (-10 <= x <= length_m + 10 and -10 <= y <= width_m + 10):
                break
            best = v
        return best

    px = np.array(
        [[0, Hh - 1], [W - 1, Hh - 1], [W - 1, top(W - 1)], [0, top(0)]], dtype=np.float64
    )
    return projector.to_m(frame, px)


def _inside(poly, p, margin: float) -> bool:
    return (
        poly is not None
        and cv2.pointPolygonTest(poly.astype(np.float32), (float(p[0]), float(p[1])), True) > margin
    )


# ---- slots ---------------------------------------------------------------------------


@dataclass
class _Visit:
    """One identity's run of sightings with no gap longer than `blink_s`."""

    pid: int
    k: int  # its number within the identity
    group: tuple
    first: int  # frames, absolute
    last: int
    seen_s: float
    start: np.ndarray  # smoothed x, y, vx, vy at first and last
    end: np.ndarray


def _group(ident) -> tuple | None:
    if ident.role == REFEREE:
        return ('', REFEREE)
    if ident.role in (PLAYER, KEEPER) and ident.team in ('team_a', 'team_b'):
        return (ident.team, ident.role)
    return None


def _bridge(a: _Visit, b: _Visit, s: MapSettings, in_view_s: float) -> float | None:
    """Cost of one person leaving the picture at a's end and coming back at b's start."""
    t = (b.first - a.last) / s.fps
    d = np.hypot(*(b.start[:2] - a.end[:2]))
    if d > s.max_speed * t + 2 * s.bridge_sigma_m:
        return None
    g = s.tau_s * (1.0 - np.exp(-t / s.tau_s))
    r = b.start[:2] - a.end[:2] - 0.5 * (a.end[2:] + b.start[2:]) * g
    var = s.bridge_sigma_m**2 + s.diffusion * t
    return 0.5 * (r @ r) / var + np.log(var / s.bridge_sigma_m**2) + s.view_cost * in_view_s


def chain(visits: list, polys: list, lo: int, hi: int, k: int, s: MapSettings):
    """Chain one group's visits into at most k slots by min-cost flow.

    Every visit wants a slot (worth `value` plus `value_per_s` a second seen);
    a slot that begins or ends mid-window pays `start_cost`; one person
    leaving and coming back pays `_bridge`; the linker's own joins (the next
    visit of the same identity) are free; and a slot may hand over to a visit
    no one could have run to in time for `jump_cost` (far-side feet are metres
    out along the camera ray, and an earlier hand-over may be wrong), which is
    drawn as one guess fading out while the other fades in. Returns [[visit, ...]] and the
    visits left out.
    """
    import networkx as nx

    visits = sorted(visits, key=lambda v: (v.first, v.pid))
    if not visits:
        return [], []
    G = nx.DiGraph()
    G.add_node('S', demand=-k)
    G.add_node('T', demand=k)
    G.add_edge('S', 'T', capacity=k, weight=0)
    edge = s.fps * 0.5
    grace = s.view_grace_s * s.fps
    sc = lambda c: int(round(c * 100))  # noqa: E731  (network simplex wants integers)
    for n, v in enumerate(visits):
        G.add_edge(
            'S', ('i', n), capacity=1, weight=sc(0.0 if v.first - lo <= edge else s.start_cost)
        )
        G.add_edge(
            ('o', n), 'T', capacity=1, weight=sc(0.0 if hi - 1 - v.last <= edge else s.start_cost)
        )
        G.add_edge(('i', n), ('o', n), capacity=1, weight=-sc(s.value + s.value_per_s * v.seen_s))
    for n, a in enumerate(visits):
        for m, b in enumerate(visits):
            if b.first <= a.last:
                continue
            if b.pid == a.pid and b.k == a.k + 1:
                G.add_edge(('o', n), ('i', m), capacity=1, weight=0)
                continue
            if _bridge(a, b, s, 0.0) is None:
                G.add_edge(('o', n), ('i', m), capacity=1, weight=sc(s.jump_cost))
                continue
            seen_s = 0.0
            if b.first - a.last > 2 * grace:
                step = max(1, int(s.fps / 3))
                for f in range(int(a.last + grace), int(b.first - grace), step):
                    w = (f - a.last) / (b.first - a.last)
                    if _inside(
                        polys[f - lo], a.end[:2] * (1 - w) + b.start[:2] * w, s.view_margin_m
                    ):
                        seen_s += step / s.fps
            G.add_edge(('o', n), ('i', m), capacity=1, weight=sc(_bridge(a, b, s, seen_s)))
    flow = nx.min_cost_flow(G)
    slots, used = [], set()
    for n in range(len(visits)):
        if flow['S'].get(('i', n), 0) <= 0:
            continue
        run = [n]
        while True:
            used.add(run[-1])
            nxt = [q for q, f in flow[('o', run[-1])].items() if f > 0 and q != 'T']
            if not nxt:
                break
            run.append(nxt[0][1])
        slots.append([visits[m] for m in run])
    return slots, [v for m, v in enumerate(visits) if m not in used]


def visits(ident, fs: np.ndarray, xs: np.ndarray, lo: int, s: MapSettings, others=None) -> list:
    """Split an identity's sightings (absolute frames fs) at gaps over `blink_s`.

    A shorter gap is a blink of the detector, drawn as seen, unless `others`
    (frame -> [(pid, group, xy)]) has someone of the same group seen on the
    blink's path: then the tracker followed this person under another
    identity meanwhile, and drawing both would show one person twice.
    """
    g = _group(ident)
    gap = np.diff(fs)
    cut = gap > s.blink_s * s.fps
    if others is not None:
        for j in np.flatnonzero((gap > 1) & ~cut):
            a, b = int(fs[j]), int(fs[j + 1])
            for f in range(a + 1, b):
                p = xs[f - lo, :2]
                if any(
                    q != ident.pid and h == g and np.hypot(*(xy - p)) < s.shadow_m
                    for q, h, xy in others.get(f, ())
                ):
                    cut[j] = True
                    break
    cut = np.flatnonzero(cut) + 1
    out = []
    for k, part in enumerate(np.split(fs, cut)):
        out.append(
            _Visit(
                ident.pid,
                k,
                g,
                int(part[0]),
                int(part[-1]),
                len(part) / s.fps,
                xs[part[0] - lo].copy(),
                xs[part[-1] - lo].copy(),
            )
        )
    return out


# ---- the map -------------------------------------------------------------------------


def build(
    res: LinkResult,
    projector: PitchProjector,
    lo: int,
    hi: int,
    length_m: float,
    width_m: float,
    settings: MapSettings | None = None,
    person_px=None,
) -> TopDown:
    s = settings or MapSettings()
    T = hi - lo
    ident = {i.pid: i for i in res.identities}
    obs = feet(res, projector, s, person_px)
    pids = sorted(obs)
    # 1. every identity alone: its visits, and where each starts and ends
    z, R, mask = _timeline([obs[p] for p in pids], lo, T)
    xs, _ = smooth(z, R, mask, s)
    drift = _team_velocity(xs, mask, [_group(ident[p]) for p in pids], s)
    polys = [
        view_polygon(projector, f, length_m, width_m) if projector.has(f) else None
        for f in range(lo, hi)
    ]
    seen_at: dict = {}
    for n, p in enumerate(pids):
        for f, xy in zip(obs[p][0], xs[n, obs[p][0] - lo, :2]):
            seen_at.setdefault(int(f), []).append((p, _group(ident[p]), xy))
    groups: dict = {}
    extras = []
    for n, p in enumerate(pids):
        vs = visits(ident[p], obs[p][0], xs[n], lo, s, seen_at)
        if vs[0].group is None:
            extras += vs
        else:
            groups.setdefault(vs[0].group, []).extend(vs)
    # 2. slots
    slots = []
    for g, vs in sorted(groups.items()):
        runs, left = chain(vs, polys, lo, hi, s.outfield if g[1] == PLAYER else 1, s)
        slots += [(g, r) for r in runs]
        extras += left
    # 3. segments: a slot's runs of plausible bridges, each smoothed as one person
    segs = []  # (slot, group, [visits])
    for n, (g, run) in enumerate(slots):
        cur = [run[0]]
        for a, b in zip(run, run[1:]):
            if _bridge(a, b, s, 0.0) is None:
                segs.append((n, g, cur))
                cur = [b]
            else:
                cur.append(b)
        segs.append((n, g, cur))

    def rows(v):
        fs = obs[v.pid][0]
        return (fs >= v.first) & (fs <= v.last)

    tracks = [
        tuple(np.concatenate([obs[v.pid][j][rows(v)] for v in run]) for j in range(3))
        for _, _, run in segs
    ]
    tracks += [tuple(obs[v.pid][j][rows(v)] for j in range(3)) for v in extras]
    z, R, mask = _timeline(tracks, lo, T)
    still = np.zeros((T, 2))
    xs, Ps = smooth(
        z,
        R,
        mask,
        s,
        np.stack(
            [drift.get(g, still) for _, g, _ in segs] + [drift.get(v.group, still) for v in extras]
        ),
    )
    bounds = (
        -s.pitch_margin_m,
        -s.pitch_margin_m,
        length_m + s.pitch_margin_m,
        width_m + s.pitch_margin_m,
    )
    xs[..., 0] = np.clip(xs[..., 0], bounds[0], bounds[2])
    xs[..., 1] = np.clip(xs[..., 1], bounds[1], bounds[3])
    # 4. who is seen where; a slot's segments hand over across a jump by fading
    N = len(tracks)
    owner = np.full((N, T), -1)
    share = np.zeros((N, T))  # how much of the slot's guess this track carries
    for n, (slot, g, run) in enumerate(segs):
        for j, v in enumerate(run):
            end = v.last
            if j + 1 < len(run) and run[j + 1].first - v.last <= s.blink_s * s.fps:
                end = run[j + 1].first - 1  # a blink between two visits of one person
            owner[n, v.first - lo : end - lo + 1] = v.pid
        prev = [m for m in range(n) if segs[m][0] == slot]
        nxt = [m for m in range(n + 1, len(segs)) if segs[m][0] == slot]
        share[n] = handover_share(
            run[0].first - lo,
            run[-1].last - lo,
            T,
            segs[prev[-1]][2][-1].last - lo if prev else None,
            segs[nxt[0]][2][0].first - lo if nxt else None,
        )
    for m, v in enumerate(extras):
        owner[len(segs) + m, v.first - lo : v.last - lo + 1] = v.pid
    group = [g for _, g, _ in segs] + [
        v.group or (ident[v.pid].team, ident[v.pid].role) for v in extras
    ]
    alpha = _ghost_alpha(xs, Ps, owner, mask, share, group, polys, s)
    slot_of = [sl for sl, _, _ in segs] + [-1] * len(extras)
    dots = [[] for _ in range(T)]
    for n in range(N):
        for t in range(T):
            if owner[n, t] >= 0:
                dots[t].append(
                    Dot(
                        xs[n, t, 0],
                        xs[n, t, 1],
                        True,
                        1.0,
                        group[n],
                        ident[owner[n, t]],
                        slot_of[n],
                    )
                )
            elif alpha[n, t] > 0.02:
                dots[t].append(
                    Dot(
                        xs[n, t, 0],
                        xs[n, t, 1],
                        False,
                        float(alpha[n, t]),
                        group[n],
                        None,
                        slot_of[n],
                    )
                )
    for t in range(T):
        dots[t] = cap_teams(dots[t], res, lo + t, s.outfield)
    return TopDown(
        lo,
        hi,
        dots,
        slots=[(g, [(v.pid, v.k) for v in r]) for g, r in slots],
        extras=[(v.pid, v.k) for v in extras],
        path=xs[..., :2],
        owner=owner,
        alpha=alpha,
        slot_of=np.array(slot_of),
        group=group,
    )


def _team_velocity(xs, mask, groups, s: MapSettings) -> dict:
    """group -> (T, 2) m/s: `team_drift` x the mean velocity of the team's players seen then.

    A team moves as a block, so a player out of the picture is best guessed
    to move with the teammates in it (on the dev clips, 10 s after a player
    left for good: 6-8 m out instead of 9-12 m standing still). Each seen
    player brings their own tracked velocity; a mean of positions would jump
    whenever the camera showed a different set of people. Keepers stay put;
    the referee follows everyone.
    """
    T = mask.shape[1]
    tot, cnt = {}, {}
    for n, g in enumerate(groups):
        if g is None or g[1] != PLAYER:
            continue
        v = np.where(mask[n, :, None], xs[n, :, 2:], 0.0)
        for key in (g[0], ''):
            tot.setdefault(key, np.zeros((T, 2)))[:] += v
            cnt.setdefault(key, np.zeros(T))[:] += mask[n]
    k = max(1, int(s.drift_s * s.fps))
    ker = np.ones(2 * k + 1) / (2 * k + 1)
    out = {}
    for key in tot:
        m = np.where(cnt[key][:, None] > 0, tot[key] / np.maximum(cnt[key], 1)[:, None], 0.0)
        m = np.stack(
            [np.convolve(np.pad(m[:, j], k, mode='edge'), ker, mode='valid') for j in range(2)], 1
        )
        if key:
            out[(key, PLAYER)] = s.team_drift * m
        else:
            out[('', REFEREE)] = s.team_drift * m
    return out


def cap_teams(dots: list, res, frame: int, outfield: int) -> list:
    """Never draw more than `outfield` of a team's outfield players in one frame.

    The linker caps the boxes of a frame (`LinkResult.team_at`), but the map
    also draws guesses for the unseen and holds a player whose box blinked out
    for up to `blink_s`, and those can take a team past the cap. Over it, the
    faintest guesses go first, then the held dots are greyed (no box this
    frame: the least evidence), and only then the boxes the linker kept. A
    greyed dot is drawn without a team rather than with a wrong one."""

    def boxed(d):
        fs = d.ident.frames
        k = np.searchsorted(fs, frame)
        return k < len(fs) and fs[k] == frame

    for d in dots:
        if d.seen and d.ident is not None and d.group[1] == PLAYER:
            d.team = res.team_at(d.ident, frame) if boxed(d) else d.group[0]
    out = list(dots)
    for team in ('team_a', 'team_b'):
        mine = [d for d in out if d.group[1] == PLAYER and d.drawn_team == team]
        extra = len(mine) - outfield
        if extra <= 0:
            continue
        ghosts = sorted((d for d in mine if not d.seen), key=lambda d: d.alpha)[:extra]
        out = [d for d in out if all(d is not g for g in ghosts)]
        extra -= len(ghosts)
        for d in [d for d in mine if d.seen and not boxed(d)][: max(extra, 0)]:
            d.team = 'unknown'
    return out


def handover_share(a: int, b: int, T: int, p_end: int | None, n_start: int | None) -> np.ndarray:
    """How much of its slot's guess a segment seen from frame a to b carries,
    when the slot's previous segment ended at p_end and its next starts at
    n_start (None: there is none, so the guess holds to the window's edge).

    The two segments are not one person's run (that would have made them one
    segment), so neither guess glides into the other: the old one fades out
    over the first half of the gap and the new one in over the second, and
    the slot is never drawn twice."""
    t = np.arange(T, dtype=float)
    out = np.zeros(T)
    out[a : b + 1] = 1.0
    if p_end is None:
        out[:a] = 1.0
    else:
        m = (p_end + a) / 2
        out[:a] = np.clip((t[:a] - m) / max(a - m, 1), 0, 1)
    if n_start is None:
        out[b + 1 :] = 1.0
    else:
        m = (b + n_start) / 2
        out[b + 1 :] = np.clip((m - t[b + 1 :]) / max(m - b, 1), 0, 1)
    return out


def ghost_alpha(sd_m):
    """How strongly to draw an unseen dot whose position is uncertain by sd_m metres."""
    return np.clip(0.9 * np.exp(-np.maximum(np.asarray(sd_m) - 0.5, 0.0) / 8.0), 0.4, 0.9)


def _ghost_alpha(xs, Ps, owner, mask, share, group, polys, s: MapSettings) -> np.ndarray:
    """Per track and frame, how strongly to draw it while unseen.

    Fades with the smoother's spread and with the track's share of its slot,
    and to nothing where the guess is contradicted: the picture has shown that
    spot for over `view_grace_s` with nobody there, or someone of the same
    group is seen on top of it (most likely the same person, under an identity
    the linker kept apart).
    """
    N, T = owner.shape
    away = _since(mask, s.fps) > s.view_grace_s
    sd = np.sqrt(np.maximum(Ps[..., 0, 0] + Ps[..., 1, 1], 0.0) / 2)
    want = ghost_alpha(sd) * share
    gid = {g: k for k, g in enumerate(sorted(set(group)))}
    gk = np.array([gid[g] for g in group])
    for t in range(T):
        ghost = (owner[:, t] < 0) & (want[:, t] > 0)
        if not ghost.any():
            continue
        seen = owner[:, t] >= 0
        for n in np.flatnonzero(ghost):
            if away[n, t] and _inside(polys[t], xs[n, t, :2], s.view_margin_m):
                want[n, t] = 0.0
                continue
            near = seen & (gk == gk[n])
            if near.any() and np.min(np.hypot(*(xs[near, t, :2] - xs[n, t, :2]).T)) < s.shadow_m:
                want[n, t] = 0.0
    # ease in and out rather than blink
    k = max(1, int(s.fade_s * s.fps))
    ker = np.ones(2 * k + 1) / (2 * k + 1)
    out = np.array([np.convolve(np.pad(w, k, mode='edge'), ker, mode='valid') for w in want])
    return np.where(owner >= 0, 0.0, out)


def _since(mask: np.ndarray, fps: float) -> np.ndarray:
    """Per entry: seconds to the nearest True in its row (inf if none)."""
    N, T = mask.shape
    out = np.full((N, T), np.inf)
    ar = np.arange(T)
    for n in range(N):
        idx = np.flatnonzero(mask[n])
        if not len(idx):
            continue
        k = np.searchsorted(idx, ar)
        lft = np.where(k > 0, ar - idx[np.maximum(k - 1, 0)], np.inf)
        rgt = np.where(k < len(idx), idx[np.minimum(k, len(idx) - 1)] - ar, np.inf)
        out[n] = np.minimum(lft, rgt) / fps
    return out
