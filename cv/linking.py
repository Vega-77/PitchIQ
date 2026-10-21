"""Joining tracklets into people, offline, with the rules a match obeys.

`cv/pitchtrack.py` hands over tracklets: runs that are one person with high
confidence, ended wherever that confidence ran out — the player walked out of
the picture, was hidden behind a teammate for a second, or crossed a teammate
too closely to say who was who. Joining them is easier offline than online,
because both sides of every gap are known: where the player was going when
the track ended *and* where the new track was coming from when it began.

The joins respect what is physically true of a football match:

* **Two tracklets seen in the same frame are two people.** A person is never
  in two places, so groups that overlap in time never merge.

* **A player runs at most ~9 m/s.** A gap that would need more is not one
  person, whatever the colours say. Far-side positions are uncertain by a few
  metres along the camera ray, and the allowance includes that.

* **Shirts don't change.** Team A never continues as team B. A tracklet whose
  kit was unreadable may join either, at a price. And a tracklet whose shirt
  does change, clearly and for more than a moment, followed two people: the
  tracker swapped somewhere it did not notice, and it is cut there.

* **The camera's footprint explains absence.** A player who leaves the
  picture can be anywhere when they come back; a player who vanished while
  the picture still showed their path, with nobody in front of them, is
  suspicious, and the join pays for every unexplained second.

* **A team has eleven players.** If more than eleven identities of one kit
  remain over a short window, joins are missing, and a second pass with a
  looser threshold looks for them — within that team only.

* **Keepers and referees are their own people.** The deepest person in
  front of each goal is that end's keeper, whatever `assign_teams` made of
  their shirt; anyone else in a kit that is neither team's is officiating; and
  anyone mostly outside the lines, or standing still on one, is not playing.

And, as everywhere in this tracker, a wrong join is worse than a missing one.
Each round is an optimal assignment (`cv/assignment.py`), and every join it
picks must win clearly: if changing it — to another end, another start, or by
swapping with a neighbouring join — costs less than `ambiguity_margin` more,
it is refused, and the two fragments stay two.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .assignment import min_cost_assignment, min_cost_matching
from .pitchtrack import (
    Detections,
    PitchProjector,
    Tracklet,
    descriptor_distance,
    descriptor_scale,
    foot_covariances,
    foot_points,
)
from .teams import TEAM_A, TEAM_B, UNKNOWN, assign_teams

PLAYER = 'player'
KEEPER = 'keeper'
REFEREE = 'referee'
OFF = 'off'  # well outside the lines most of the time: coach, sub, fan

# A kit that is confidently neither team's. `assign_teams` splits on one axis
# and puts everyone on a side of it; a neon referee is "light" on the
# lightness axis that separates a black kit from a white one, and lands in the
# white team. Its shirt colour, though, is nothing like either team's.
OTHER = 'other'


@dataclass
class LinkSettings:
    fps: float = 30.0
    max_speed: float = 8.5  # m/s, sustained, over a gap
    reach_slack_m: float = 2.0
    reach_sigmas: float = 2.0  # how many of the two ends' combined foot sigmas
    reach_rss: bool = False  # combine as root-sum-square, not a plain sum
    stage_windows_s: tuple = (0.5, 2.0, 5.0, 20.0)
    threshold: float = 9.0  # a join must cost less than this
    # 4 until the bib fix: with bibs on their own team, a bib's piece may join
    # any white shirt, and same-team joins are where this tracker goes wrong.
    # At 6, over three jittered runs, fm01 wrong-person 0.055 -> 0.037 for
    # 0.01 IDF1; fr00 unchanged. 8 bought nothing more.
    ambiguity_margin: float = 6.0
    unknown_team_penalty: float = 3.0
    appearance_weight: float = 0.5
    # Cosine distance between the mean detector features (Detections.feat)
    # of one piece's end and the next one's start, in cost units per 0.1.
    # Used only when the detections carry features; then the ambiguity
    # margin is `feature_margin`, since the features have already told the
    # close calls apart. Over three jittered runs, 4 and 3 against none and
    # 6: fm01 IDF1 0.799 -> 0.821, wrong-person 0.034 -> 0.020; fr00 0.732
    # -> 0.751, 0.067 -> 0.033. A lower threshold alone, at as few joins,
    # lost IDF1 and kept more wrong ones.
    feature_weight: float = 4.0
    feature_cap: float = 9.0
    feature_margin: float = 3.0
    motion_sigma0: float = 0.6  # m
    motion_sigma_per_s: float = 1.2  # m per second of gap
    velocity_horizon_s: float = 1.0  # extrapolate at most this far
    unseen_grace_s: float = 1.0
    unseen_penalty_per_s: float = 1.5
    occluder_radius_m: float = 1.5
    view_margin_px: float = 20.0
    team_cap: int = 11
    cap_window_s: float = 120.0  # the cap pass only makes sense over short windows
    # Forcing joins until a kit has eleven assumes every shirt was read
    # right; one player in a navy bib over white reads as the black team and
    # the forced joins are guesses (on the dev window: 3 right, 3 wrong). Off.
    cap_relax: tuple = ()
    off_margin_m: float = 3.0
    line_band_m: float = 1.0  # on or beyond a line, give or take a metre
    still_spread_m: float = 1.5  # median distance from their own median spot
    still_min_s: float = 1.0
    off_share: float = 0.8
    # Touchlines only (a keeper lives near a goal line): the far touchline is
    # uncertain by a couple of metres along the ray, and a bench there reads
    # three or four metres inside the pitch. Near = within line_band_m plus
    # this many foot sigmas; still = spread under this many sigmas.
    touchline_sigmas: float = 2.0
    touchline_still_sigmas: float = 1.5
    # An assistant referee walks the line for the whole window, and a sub
    # paces up and down at the bench: on a touchline this much of the time,
    # for at least this long, is not playing, moving or not.
    touchline_live_share: float = 0.95
    touchline_live_s: float = 10.0
    keeper_depth_m: float = 8.0  # median spot this close to the goal line
    keeper_half_width_m: float = 20.0  # and within the box's width of centre
    keeper_min_s: float = 3.0
    fill_gap_s: float = 1.0  # interpolate joined gaps up to this long
    end_fit_n: int = 15  # observations for an end's velocity
    other_kit_ab: float = 25.0  # Lab a-b distance from both teams' shirts
    other_kit_spread: float = 4.0  # ... and this many of the teams' own spreads
    other_kit_min_s: float = 0.5  # seen this long before it can be called
    # Cut a tracklet where its shirt reads as the other kit for a sustained
    # stretch: a rolling median over `kit_cut_win` rows, at least
    # `kit_cut_rows` rows on each side. Lean is 0 halfway between the kits and
    # 1 at a kit's centre. A white shirt with its back to the sun reads dark for
    # seconds (B6 on the dev window, lean -1.0), but nothing makes a black
    # shirt read bright white: on the lightness axis the light side must reach
    # `kit_cut_sure` and the dark side only `kit_cut_lean`. On chroma both
    # sides must be sure. 0 rows turns it off.
    kit_cut_rows: int = 20
    kit_cut_win: int = 15
    kit_cut_lean: float = 0.3
    kit_cut_sure: float = 0.9
    # The rows between two sustained runs are the moment of the swap and are
    # dropped. A gap at least `kit_cut_keep_rows` long is not a moment (a shirt
    # that reads neither kit for seconds): it becomes its own piece instead,
    # joined or not like any other. 0 drops every gap. On the vid1 lab (set
    # vid1-gaps, 2026-10-12) the ring stayed on one person at 44 of 45 ends of
    # kept gaps, and the kept rows were 3% of vid1's on-pitch detections.
    kit_cut_keep_rows: int = 30
    # Eleven a side, one in goal: never more than this many outfield players
    # of one team in a frame. Where the kits say more, the least sure of them
    # (median lean toward their own kit, then fewer rows) are shown as unsure
    # in that frame only; their identity keeps its team everywhere else.
    # 0 turns it off.
    outfield_cap: int = 10
    # Somebody who appears mid-window is new, or someone seen before whose
    # return no join was sure of. If anybody earlier fits under
    # `guess_doubt_cost`, `LinkResult.label` shows them as "?", or as
    # "12?" when one earlier identity that left within `guess_window_s` is a
    # clear best fit: costs under `guess_sure_cost` (beating the runner-up by
    # `guess_sure_margin`), or under `guess_cost` beating it by
    # `guess_margin`. On fm01 + fr00, 10 of 10 such guesses named the right
    # person; the cheapest candidate without those bounds was right ~65%.
    guess_window_s: float = 60.0
    guess_sure_cost: float = 9.0
    guess_sure_margin: float = 1.0
    guess_cost: float = 15.0
    guess_margin: float = 4.0
    guess_after_s: float = 1.0  # who is there in the first second is not new
    guess_doubt_cost: float = 15.0  # "?" only if somebody earlier fits this well
    # A long gap fits almost anywhere: its motion sigma is so wide that a
    # 16-second absence cost the same as a 2.7-second one two metres away, and
    # the right join was refused as ambiguous. A Gaussian's own normaliser
    # charges for that vagueness: 4 ln(sigma / sigma at no gap), times this.
    gap_vagueness_weight: float = 0.0
    # The roster pass: after the stages, per kit, one more matching over the
    # whole window in which a team has `team_cap` people and each one beyond
    # that costs this much. "The returning player is somebody new" is no
    # longer free once the team is full. Each join must still win by
    # `ambiguity_margin` over the best arrangement without it. 0 = off.
    roster_penalty: float = 0.0


@dataclass
class _Piece:
    """One tracklet, summarised for joining."""

    idx: int
    rows: np.ndarray
    frames: np.ndarray
    pos: np.ndarray  # (n, 2) metres
    sigma: np.ndarray  # (n,) metres, worst-axis foot uncertainty
    first: int
    last: int
    p0: np.ndarray
    v0: np.ndarray
    p1: np.ndarray
    v1: np.ndarray
    s0: float
    s1: float
    desc0: np.ndarray
    desc1: np.ndarray
    feat0: np.ndarray | None = None
    feat1: np.ndarray | None = None
    kit: str = UNKNOWN


@dataclass
class Identity:
    """One person across the window."""

    pid: int
    team: str  # team_a / team_b / unknown
    role: str  # player / keeper / referee / off
    frames: np.ndarray  # sorted absolute frames
    boxes: np.ndarray  # (n, 4) pixels
    conf: np.ndarray  # (n,) 0 for interpolated
    interp: np.ndarray  # (n,) bool
    rows: np.ndarray  # detection row, -1 when interpolated
    pieces: list = field(default_factory=list)

    def box_at(self, frame: int):
        i = np.searchsorted(self.frames, frame)
        if i < len(self.frames) and self.frames[i] == frame:
            return self.boxes[i]
        return None


@dataclass
class LinkResult:
    identities: list
    joins: list  # (stage, end piece, start piece, cost)
    refused: list  # (stage, end piece, start piece, cost, why)
    team_counts: dict
    defends_left: str | None  # which team keeps the x=0 goal
    unsure: dict = field(default_factory=dict)  # frame -> pids over the outfield cap there
    guess: dict = field(default_factory=dict)  # pid -> the earlier pid it probably is, or None

    def label(self, ident: Identity) -> str:
        """What to call the identity: its number, or "?" / "12?" if it may be someone seen before."""
        if ident.pid not in self.guess:
            return str(ident.pid)
        g = self.guess[ident.pid]
        return '?' if g is None else f'{g}?'

    def team_at(self, ident: Identity, frame: int) -> str:
        """The identity's team in this frame: unknown where the cap took it out."""
        return UNKNOWN if ident.pid in self.unsure.get(frame, ()) else ident.team

    def at(self, frame: int) -> list[tuple[Identity, np.ndarray]]:
        out = []
        for ident in self.identities:
            b = ident.box_at(frame)
            if b is not None:
                out.append((ident, b))
        return out


def _torso_covered(det: Detections, share: float = 0.2) -> np.ndarray:
    """Per detection: is its torso partly behind someone nearer the camera?

    The shirt read off such a box is partly the other person's, which is how a
    white shirt passing behind a black one reads black for a few seconds.
    """
    out = np.zeros(len(det), dtype=bool)
    for rows in det.rows_by_frame().values():
        if len(rows) < 2:
            continue
        b = det.box[rows]
        h = b[:, 3] - b[:, 1]
        tx1, tx2 = b[:, 0], b[:, 2]
        ty1, ty2 = b[:, 1] + 0.15 * h, b[:, 1] + 0.55 * h
        area = np.maximum((tx2 - tx1) * (ty2 - ty1), 1e-6)
        ix = np.clip(
            np.minimum(tx2[:, None], b[None, :, 2]) - np.maximum(tx1[:, None], b[None, :, 0]),
            0,
            None,
        )
        iy = np.clip(
            np.minimum(ty2[:, None], b[None, :, 3]) - np.maximum(ty1[:, None], b[None, :, 1]),
            0,
            None,
        )
        nearer = b[None, :, 3] > b[:, None, 3]  # feet lower in the picture: in front
        hit = (ix * iy / area[:, None] > share) & nearer
        np.fill_diagonal(hit, False)
        out[rows] = hit.any(axis=1)
    return out


def _unit_mean(feat: np.ndarray | None, rows: np.ndarray) -> np.ndarray | None:
    """Mean of some rows' feature vectors, scaled to unit length."""
    if feat is None or not len(rows):
        return None
    m = np.asarray(feat[rows], dtype=np.float64).mean(axis=0)
    norm = np.linalg.norm(m)
    return m / norm if norm > 0 else None


def _end_fit(frames: np.ndarray, pos: np.ndarray, fps: float, max_speed: float):
    """Position and velocity at the last observation, by least squares."""
    if len(frames) < 3:
        return pos[-1].copy(), np.zeros(2)
    t = (frames - frames[-1]) / fps
    A = np.c_[np.ones_like(t), t]
    coef, *_ = np.linalg.lstsq(A, pos, rcond=None)
    p, v = coef[0], coef[1]
    speed = np.hypot(*v)
    if speed > max_speed:
        v = v * (max_speed / speed)
    return p, v


class Linker:
    def __init__(
        self,
        projector: PitchProjector,
        pitch_length: float,
        pitch_width: float,
        settings: LinkSettings | None = None,
    ) -> None:
        self.proj = projector
        self.L = float(pitch_length)
        self.W = float(pitch_width)
        self.s = settings or LinkSettings()

    # ---- summaries
    def _pieces(self, tracklets: list[Tracklet], det: Detections) -> list[_Piece]:
        s = self.s
        out = []
        for i, tr in enumerate(tracklets):
            rows = np.asarray(tr.rows)
            frames = det.frame[rows]
            pos = np.empty((len(rows), 2))
            sig = np.empty(len(rows))
            for k, (f, r) in enumerate(zip(frames, rows)):
                pos[k] = self.proj.to_m(f, foot_points(det.box[r]))[0]
                cov = foot_covariances(self.proj, f, det.box[r][None])[0]
                sig[k] = float(np.sqrt(np.linalg.eigvalsh(cov)[-1]))
            n = s.end_fit_n
            p1, v1 = _end_fit(frames[-n:], pos[-n:], s.fps, s.max_speed)
            pr, vr = _end_fit(frames[:n][::-1], pos[:n][::-1], s.fps, s.max_speed)
            # fitted backwards in time: the velocity sign flips back
            d = det.desc[rows]
            with np.errstate(all='ignore'):
                desc0 = np.nanmedian(d[:n], axis=0) if len(d) else np.full(6, np.nan)
                desc1 = np.nanmedian(d[-n:], axis=0) if len(d) else np.full(6, np.nan)
            out.append(
                _Piece(
                    idx=i,
                    rows=rows,
                    frames=frames,
                    pos=pos,
                    sigma=sig,
                    first=int(frames[0]),
                    last=int(frames[-1]),
                    p0=pr,
                    v0=-vr,
                    p1=p1,
                    v1=v1,
                    s0=float(np.median(sig[:n])),
                    s1=float(np.median(sig[-n:])),
                    desc0=desc0,
                    desc1=desc1,
                    feat0=_unit_mean(det.feat, rows[:n]),
                    feat1=_unit_mean(det.feat, rows[-n:]),
                )
            )
        return out

    def _kits(self, pieces: list[_Piece], det: Detections) -> None:
        if det.kit is None:
            for p in pieces:
                votes = det.team[p.rows]
                a, b = int(np.sum(votes == 0)), int(np.sum(votes == 1))
                p.kit = TEAM_A if a >= 2 * max(b, 1) else TEAM_B if b >= 2 * max(a, 1) else UNKNOWN
            return
        samples = {}
        for p in pieces:
            k = det.kit[p.rows]
            k = k[np.isfinite(k).any(axis=1)]
            if len(k):
                samples[p.idx] = list(k)
        assignment = assign_teams(samples, weights={p.idx: float(len(p.rows)) for p in pieces})
        self._assignment = assignment
        for p in pieces:
            p.kit = assignment.team_of(p.idx)
        # On the lightness axis a shirt further out than a kit's own centre is
        # that kit, not a stranger: assign_teams calls a sunlit white shirt
        # (L 254) too far from the white centre to be sure, and an unsure piece
        # may join either team, so it was joined between two black ones. Shirts
        # in a third colour are still caught on chroma, by _other_kits.
        if assignment.axis == 'grass-relative lightness' and assignment.axis_centres is not None:
            ca, cb = assignment.axis_centres[:, 0]
            for p in pieces:
                if p.kit == UNKNOWN and p.idx in samples:
                    x = float(np.nanmedian(assignment.axis_values(np.asarray(samples[p.idx]))))
                    if (x - ca) * (ca - cb) > 0:
                        p.kit = TEAM_A
                    elif (x - cb) * (cb - ca) > 0:
                        p.kit = TEAM_B
        self._other_kits(pieces, det)

    def _kit_cut(self, tracklets: list[Tracklet], det: Detections, assignment) -> list[Tracklet]:
        """Split tracklets whose shirt changes kit for good part-way through.

        The tracker cuts where a swap was a near thing at the time; this finds
        the swaps it did not notice, after the fact, from the shirt alone. The
        rows between the two sustained runs are dropped: they are the moment of
        the swap, and could be either person.
        """
        s = self.s
        covered = _torso_covered(det)
        need = [s.kit_cut_sure, s.kit_cut_sure]  # team_a side, team_b side
        if assignment.axis == 'grass-relative lightness':
            light = 1 if assignment.axis_centres[1, 0] > assignment.axis_centres[0, 0] else 0
            need[1 - light] = s.kit_cut_lean
        out = []
        next_tid = max((tr.tid for tr in tracklets), default=0) + 1
        for tr in tracklets:
            rows = np.asarray(tr.rows)
            lean = assignment.lean(det.kit[rows])
            lean[covered[rows]] = np.nan
            n = len(rows)
            if n < 2 * s.kit_cut_rows:
                out.append(tr)
                continue
            h = s.kit_cut_win // 2
            smooth = np.full(n, np.nan)
            with np.errstate(all='ignore'):
                for k in range(n):
                    w = lean[max(0, k - h) : k + h + 1]
                    if np.isfinite(w).sum() > h:
                        smooth[k] = np.nanmedian(w)
            side = np.where(smooth >= need[1], 1, np.where(smooth <= -need[0], -1, 0))
            # runs of one side, long enough to be a person and not a blink
            runs = []
            k = 0
            while k < n:
                j = k
                while j + 1 < n and side[j + 1] == side[k]:
                    j += 1
                if side[k] != 0 and j - k + 1 >= s.kit_cut_rows:
                    if runs and runs[-1][2] == side[k]:
                        runs[-1][1] = j
                    else:
                        runs.append([k, j, side[k]])
                k = j + 1
            if len(runs) < 2:
                out.append(tr)
                continue
            bounds = [0] + [r[0] for r in runs[1:]] + [n]
            for p in range(len(runs)):
                if p:
                    next_tid += 1
                lo_k = bounds[p] if p == 0 else runs[p][0]
                hi_k = runs[p][1] + 1 if p < len(runs) - 1 else n
                out.append(
                    Tracklet(
                        tid=next_tid - 1 if p else tr.tid,
                        rows=rows[lo_k:hi_k],
                        end_reason=tr.end_reason if p == len(runs) - 1 else 'cut',
                        start_reason=tr.start_reason if p == 0 else 'cut',
                    )
                )
                gap = runs[p + 1][0] - hi_k if p < len(runs) - 1 else 0
                if s.kit_cut_keep_rows and gap >= s.kit_cut_keep_rows:
                    out.append(
                        Tracklet(
                            tid=next_tid,
                            rows=rows[hi_k : hi_k + gap],
                            end_reason='cut',
                            start_reason='cut',
                        )
                    )
                    next_tid += 1
        return out

    def _other_kits(self, pieces: list[_Piece], det: Detections) -> None:
        """Mark pieces whose shirt chroma sits far from both teams' as OTHER.

        Each team's shirt is the frame-weighted median of its pieces' median
        Lab (a, b); its spread is the weighted median distance from that. A
        piece is OTHER when it is further than `other_kit_ab` *and*
        `other_kit_spread` spreads from both. Chroma only: lightness is what
        shadow changes, and what `assign_teams` already split on.
        """
        s = self.s
        ab = det.desc[:, 1:3] if det.desc is not None and len(det.desc) else None
        if ab is None:
            return
        med, seen = {}, {}
        for p in pieces:
            v = ab[p.rows]
            v = v[np.isfinite(v).all(axis=1)]
            seen[p.idx] = len(v)
            if len(v):
                med[p.idx] = np.median(v, axis=0)
        teams = {}
        for kit in (TEAM_A, TEAM_B):
            mine = [p for p in pieces if p.kit == kit and p.idx in med]
            if len(mine) < 3:
                return
            pts = np.array([med[p.idx] for p in mine])
            w = np.array([seen[p.idx] for p in mine], dtype=np.float64)
            centre = np.array([_weighted_median(pts[:, 0], w), _weighted_median(pts[:, 1], w)])
            spread = _weighted_median(np.hypot(*(pts - centre).T), w)
            teams[kit] = (centre, max(spread, 1.0))
        for p in pieces:
            if p.idx not in med or seen[p.idx] < s.other_kit_min_s * s.fps:
                continue
            far = all(
                np.hypot(*(med[p.idx] - c)) > max(s.other_kit_ab, s.other_kit_spread * sp)
                for c, sp in teams.values()
            )
            if far:
                p.kit = OTHER

    # ---- the cost of one join
    def _cost(self, a: _Piece, b: _Piece, occupancy) -> float:
        """End of `a` continues as start of `b`; inf if impossible."""
        s = self.s
        if b.first <= a.last:
            return np.inf
        if a.kit != UNKNOWN and b.kit != UNKNOWN and a.kit != b.kit:
            return np.inf
        gap = (b.first - a.last) / s.fps
        d = float(np.hypot(*(b.p0 - a.p1)))
        unsure = np.hypot(a.s1, b.s0) if s.reach_rss else a.s1 + b.s0
        if d > s.max_speed * gap + s.reach_slack_m + s.reach_sigmas * unsure:
            return np.inf
        h = min(gap / 2.0, s.velocity_horizon_s)
        m1 = a.p1 + a.v1 * h
        m2 = b.p0 - b.v0 * h
        sigma = s.motion_sigma0 + s.motion_sigma_per_s * gap + a.s1 + b.s0
        cost = float(np.sum((m1 - m2) ** 2)) / sigma**2
        if s.gap_vagueness_weight:
            cost += s.gap_vagueness_weight * 4.0 * np.log(sigma / (s.motion_sigma0 + a.s1 + b.s0))
        if a.kit != b.kit:
            cost += s.unknown_team_penalty
        cost += s.appearance_weight * min(descriptor_distance(a.desc1, b.desc0, self.scale), 9.0)
        if s.feature_weight and a.feat1 is not None and b.feat0 is not None:
            cost += min(s.feature_weight * 10.0 * (1.0 - float(a.feat1 @ b.feat0)), s.feature_cap)
        cost += self._unseen_cost(a, b, occupancy)
        return cost

    def _unseen_cost(self, a: _Piece, b: _Piece, occupancy) -> float:
        """Seconds the straight path was in view with nobody there to hide it."""
        s = self.s
        frames = np.arange(a.last + 1, b.first)
        if len(frames) <= s.unseen_grace_s * s.fps:
            return 0.0
        w = (frames - a.last) / (b.first - a.last)
        path = a.p1[None] * (1 - w[:, None]) + b.p0[None] * w[:, None]
        seen = self.proj.in_view_many(frames, path, s.view_margin_px)
        if not seen.any():
            return 0.0
        crowd, f0 = occupancy
        k = frames[seen] - f0
        inside = (k >= 0) & (k < len(crowd))
        hidden = np.zeros(int(seen.sum()), dtype=bool)
        if inside.any():
            d = np.hypot(*(crowd[k[inside]] - path[seen][inside][:, None, :]).transpose(2, 0, 1))
            hidden[inside] = (
                np.nanmin(np.where(np.isfinite(d), d, np.inf), axis=1) <= s.occluder_radius_m
            )
        unseen = int((~hidden).sum())
        extra = unseen / s.fps - s.unseen_grace_s
        return s.unseen_penalty_per_s * max(extra, 0.0)

    # ---- rounds
    def _join_costs(self, heads, groups, pieces, occupancy, window_s) -> np.ndarray:
        """Cost of each group's end continuing as each group's start."""
        s = self.s
        C = np.full((len(heads), len(heads)), np.inf)
        for i, ge in enumerate(heads):
            a = pieces[groups[ge][-1]]
            for j, gs in enumerate(heads):
                if ge == gs:
                    continue
                b = pieces[groups[gs][0]]
                if b.first <= a.last or (b.first - a.last) / s.fps > window_s:
                    continue
                # groups are runs in time, so end-before-start means no frame
                # is shared: two tracklets seen together are never one person
                if self._kit_clash(groups[ge], groups[gs], pieces):
                    continue
                if self._role_clash(groups[ge], groups[gs]):
                    continue
                key = (a.idx, b.idx)
                if key not in self._cache:
                    self._cache[key] = self._cost(a, b, occupancy)
                C[i, j] = self._cache[key]
        return C

    def _round(self, groups, pieces, occupancy, window_s, threshold, only_kit=None):
        """One optimal round of joins between group ends and group starts."""
        heads = sorted(groups)  # group id -> list of piece idx
        ends = [
            g for g in heads if only_kit is None or self._group_kit(groups[g], pieces) == only_kit
        ]
        starts = list(ends)
        C = self._join_costs(ends, groups, pieces, occupancy, window_s)
        pairs = min_cost_matching(C, unmatched=threshold / 2.0)
        chosen = dict(pairs)
        taken_cols = set(chosen.values())
        keep, refused = [], []
        for i, j in pairs:
            c = C[i, j]
            alts = [C[i, jj] - c for jj in range(C.shape[1]) if jj != j and jj not in taken_cols]
            alts += [C[ii, j] - c for ii in range(C.shape[0]) if ii != i and ii not in chosen]
            for other_i, other_j in pairs:
                if other_i != i:
                    alts.append(C[i, other_j] + C[other_i, j] - c - C[other_i, other_j])
            alts = [x for x in alts if np.isfinite(x)]
            why = None
            if alts and min(alts) < self.margin:
                why = 'ambiguous by %.2f' % min(alts)
            if why:
                refused.append((ends[i], starts[j], float(c), why))
            else:
                keep.append((ends[i], starts[j], float(c)))
        return keep, refused

    def _roster_round(self, groups, pieces, occupancy, kit, window_s):
        """One matching of a kit's groups in which the team has `team_cap` people.

        The square problem: rows are group ends and "somebody new" rows,
        columns are group starts and "nobody after" columns. A start taken by
        a "somebody new" row is a person's first appearance; `team_cap` of
        those cost `threshold / 2` like any unmatched start, and each one
        beyond costs `roster_penalty` more. So the count is what is priced,
        not which start is new — that is decided by the joins around it.

        A join is kept only if the best arrangement without it costs at least
        `ambiguity_margin` more: found by solving again with it forbidden,
        so the alternative includes everything that has to move with it.
        """
        s = self.s
        heads = [
            g
            for g in sorted(groups)
            if self._group_kit(groups[g], pieces) == kit
            and self._role_raw(groups[g], pieces) != OFF
        ]
        n = len(heads)
        if n < 2:
            return [], []
        C = self._join_costs(heads, groups, pieces, occupancy, window_s)
        if not np.isfinite(C).any():
            return [], []
        u = s.threshold / 2.0
        big = np.full((2 * n, 2 * n), np.inf)
        big[:n, :n] = C
        big[:n, n:][np.arange(n), np.arange(n)] = u  # nobody after this end
        new = np.where(np.arange(n) < s.team_cap, u, u + s.roster_penalty)
        big[n:, :n] = new[:, None]  # somebody new
        big[n:, n:] = 0.0

        def solve(m):
            pairs = min_cost_assignment(m)
            return pairs, float(sum(m[i, j] for i, j in pairs))

        pairs, total = solve(big)
        joins = [(i, j) for i, j in pairs if i < n and j < n]
        keep, refused = [], []
        for i, j in joins:
            alt = big.copy()
            alt[i, j] = np.inf
            margin = solve(alt)[1] - total
            c = float(C[i, j])
            if margin < self.margin:
                refused.append((heads[i], heads[j], c, 'ambiguous by %.2f' % margin))
            else:
                keep.append((heads[i], heads[j], c))
        return keep, refused

    @staticmethod
    def _group_kit(members, pieces) -> str:
        n = {TEAM_A: 0, TEAM_B: 0, OTHER: 0}
        for m in members:
            if pieces[m].kit in n:
                n[pieces[m].kit] += len(pieces[m].frames)
        top = sorted(n.values())
        if top[-1] == 0 or top[-1] == top[-2]:
            return UNKNOWN
        return max(n, key=n.get)

    def _role_clash(self, g1, g2) -> bool:
        """Someone not playing never continues as someone clearly playing."""
        w1 = {self._where[m] for m in g1}
        w2 = {self._where[m] for m in g2}
        return ('off' in w1 and 'on' in w2) or ('on' in w1 and 'off' in w2)

    def _piece_where(self, p: _Piece) -> str:
        """'off' (judged not playing), 'on' (well inside the lines), or ''.

        'On' needs no minimum length: a third of a second eight metres inside
        the touchline is enough to say it is not the coach on that line, and
        it is exactly the short pieces that otherwise got joined to the coach.
        """
        s = self.s
        if self._role_raw([p.idx], {p.idx: p}) == OFF:
            return 'off'
        x, y = p.pos[:, 0], p.pos[:, 1]
        inside_by = np.minimum.reduce([x, self.L - x, y, self.W - y])
        if np.median(inside_by) > s.off_margin_m + float(np.median(p.sigma)):
            return 'on'
        return ''

    @staticmethod
    def _kit_clash(g1, g2, pieces) -> bool:
        k1 = {pieces[m].kit for m in g1} - {UNKNOWN}
        k2 = {pieces[m].kit for m in g2} - {UNKNOWN}
        return len(k1 | k2) > 1

    # ---- main
    def link(self, tracklets: list[Tracklet], det: Detections) -> LinkResult:
        s = self.s
        self.scale = descriptor_scale(det.desc)
        self.margin = (
            s.feature_margin if s.feature_weight and det.feat is not None else s.ambiguity_margin
        )
        pieces = self._pieces(tracklets, det)
        self._assignment = None
        self._kits(pieces, det)
        if s.kit_cut_rows and self._assignment is not None:
            cut = self._kit_cut(tracklets, det, self._assignment)
            if len(cut) != len(tracklets):
                tracklets = cut
                pieces = self._pieces(tracklets, det)
                self._kits(pieces, det)

        # where every detection is, for "somebody was standing in the way":
        # (frames, most people in a frame, 2), NaN-padded
        by_frame = {f: r for f, r in det.rows_by_frame().items() if self.proj.has(f)}
        f0 = min(by_frame) if by_frame else 0
        crowd = np.full(
            (
                (max(by_frame) - f0 + 1) if by_frame else 0,
                max((len(r) for r in by_frame.values()), default=1),
                2,
            ),
            np.nan,
        )
        for f, rows in by_frame.items():
            crowd[f - f0, : len(rows)] = self.proj.to_m(f, foot_points(det.box[rows]))
        occupancy = (crowd, f0)
        self._occupancy = occupancy
        self._cache = {}
        self._where = {p.idx: self._piece_where(p) for p in pieces}

        groups = {p.idx: [p.idx] for p in pieces}
        joins, refused = [], []

        alias: dict[int, int] = {}

        def root(g):
            while g in alias:
                g = alias[g]
            return g

        def apply(keep, stage):
            # one round may chain a->b and b->c; joins are end-to-start and
            # strictly later in time, so members stay in time order
            for ge, gs, c in keep:
                joins.append((stage, pieces[groups[root(ge)][-1]].idx, gs, c))
                r = root(ge)
                groups[r] = sorted(groups[r] + groups.pop(gs), key=lambda m: pieces[m].first)
                alias[gs] = r

        for w in s.stage_windows_s:
            while True:
                keep, ref = self._round(groups, pieces, occupancy, w, s.threshold)
                refused += [('%.1fs' % w,) + r for r in ref]
                if not keep:
                    break
                apply(keep, '%.1fs' % w)

        span = (
            max((p.last for p in pieces), default=0) - min((p.first for p in pieces), default=0)
        ) / s.fps
        if s.roster_penalty:
            for kit in (TEAM_A, TEAM_B):
                while True:
                    keep, ref = self._roster_round(groups, pieces, occupancy, kit, span + 1.0)
                    refused += [('roster',) + r for r in ref]
                    if not keep:
                        break
                    apply(keep, 'roster')
        if span <= s.cap_window_s:
            for kit in (TEAM_A, TEAM_B):
                for relax in s.cap_relax:
                    if self._count_team(groups, pieces, kit) <= s.team_cap:
                        break
                    while self._count_team(groups, pieces, kit) > s.team_cap:
                        keep, ref = self._round(
                            groups, pieces, occupancy, span + 1.0, s.threshold * relax, only_kit=kit
                        )
                        if not keep:
                            break
                        apply(keep, 'cap x%.1f' % relax)

        return self._identities(groups, pieces, det, joins, refused)

    def _count_team(self, groups, pieces, kit) -> int:
        n = 0
        for members in groups.values():
            if self._group_kit(members, pieces) == kit and self._role_raw(members, pieces) != OFF:
                n += 1
        return n

    def _role_raw(self, members, pieces) -> str:
        """`off` for whoever is not in the match, judged on position alone.

        Two ways to be off. Well outside the lines most of the time — warming
        up behind a goal. Or standing on the line, still — the bench, a coach,
        parents along the far touchline. The pipeline's own version of the
        second test (`cv/participants.py`, "has not moved a body length")
        needs twenty seconds of screen time because it works in pixels; in
        metres it can be asked directly. A winger hugging the touchline moves;
        a throw-in taker is on the line for seconds, not for most of a window.

        A group is judged piece by piece first: a coach still for a while, then
        a few metres along the line, then still again is two still pieces but
        not one still person.
        """
        s = self.s
        if len(members) > 1:
            n = sum(len(pieces[m].pos) for m in members)
            off = sum(len(pieces[m].pos) for m in members if self._role_raw([m], pieces) == OFF)
            if off >= s.off_share * n:
                return OFF
        pos = np.vstack([pieces[m].pos for m in members])
        x, y = pos[:, 0], pos[:, 1]
        m = s.off_margin_m
        outside = (x < -m) | (x > self.L + m) | (y < -m) | (y > self.W + m)
        if outside.mean() >= s.off_share:
            return OFF
        # Far-side positions are uncertain by a couple of metres along the
        # ray, so "on the line" and "still" both allow for that uncertainty;
        # otherwise the far touchline's bench reads as players inside the pitch.
        sig = np.concatenate([pieces[m].sigma for m in members])
        inside_by = np.minimum.reduce([x, self.L - x, y, self.W - y])
        spread = float(np.median(np.hypot(*(pos - np.median(pos, axis=0)).T)))
        on_line = (inside_by < s.line_band_m + sig).mean()
        if (
            len(pos) >= s.still_min_s * s.fps
            and on_line >= s.off_share
            and spread < max(s.still_spread_m, float(np.median(sig)))
        ):
            return OFF
        side = np.minimum(y, self.W - y)
        near = (side < s.line_band_m + s.touchline_sigmas * sig).mean()
        if (
            len(pos) >= s.still_min_s * s.fps
            and near >= s.off_share
            and spread < max(s.still_spread_m, s.touchline_still_sigmas * float(np.median(sig)))
        ):
            return OFF
        if near >= s.touchline_live_share and len(pos) >= s.touchline_live_s * s.fps:
            return OFF
        return PLAYER

    def _keepers(self, ordered, roles, pieces) -> dict:
        """Group id -> 'left' / 'right': per goal, the deepest centred person.

        Kit can't be relied on: a keeper's shirt is neither team's, but on
        the lightness axis that separates these two kits, a green keeper sits
        with the dark team. Position can: nobody else spends the window within
        a few metres of their own goal line, in front of the goal.
        """
        s = self.s
        best: dict[str, tuple] = {}
        for g, members in ordered:
            if roles[g] != PLAYER:
                continue
            pos = np.vstack([pieces[m].pos for m in members])
            if len(pos) < s.keeper_min_s * s.fps:
                continue
            cx, cy = np.median(pos, axis=0)
            if abs(cy - self.W / 2) > s.keeper_half_width_m:
                continue
            for end, depth in (('left', cx), ('right', self.L - cx)):
                if -s.off_margin_m < depth < s.keeper_depth_m and (
                    end not in best or depth < best[end][1]
                ):
                    best[end] = (g, depth)
        return {g: end for end, (g, _) in best.items()}

    def _identities(self, groups, pieces, det, joins, refused) -> LinkResult:
        s = self.s
        # which team keeps which goal: the team whose players stand nearer x=0
        xs = {TEAM_A: [], TEAM_B: []}
        for members in groups.values():
            kit = self._group_kit(members, pieces)
            if kit in xs and self._role_raw(members, pieces) == PLAYER:
                xs[kit].append(np.vstack([pieces[m].pos for m in members])[:, 0])
        defends_left = None
        if xs[TEAM_A] and xs[TEAM_B]:
            ma, mb = np.mean(np.concatenate(xs[TEAM_A])), np.mean(np.concatenate(xs[TEAM_B]))
            defends_left = TEAM_A if ma < mb else TEAM_B
        other = {TEAM_A: TEAM_B, TEAM_B: TEAM_A}

        idents = []
        ordered = sorted(groups.items(), key=lambda kv: pieces[kv[1][0]].first)
        roles = {g: self._role_raw(members, pieces) for g, members in ordered}
        keepers = self._keepers(ordered, roles, pieces)
        for pid, (g, members) in enumerate(ordered):
            kit = self._group_kit(members, pieces)
            role = roles[g]
            team = kit
            if g in keepers:
                role = KEEPER
                team = (
                    defends_left if keepers[g] == 'left' else other.get(defends_left)
                ) or UNKNOWN
            elif role == PLAYER and kit == OTHER:
                role = REFEREE
                team = UNKNOWN

            frames, boxes, conf, interp, rows = [], [], [], [], []
            prev = None
            for m in members:
                p = pieces[m]
                if prev is not None:
                    gap = p.first - prev.last
                    if 1 < gap <= s.fill_gap_s * s.fps:
                        self._fill(prev, p, det, frames, boxes, conf, interp, rows)
                frames += p.frames.tolist()
                boxes += list(det.box[p.rows])
                conf += det.conf[p.rows].tolist()
                interp += [False] * len(p.rows)
                rows += p.rows.tolist()
                prev = p
            order = np.argsort(frames, kind='stable')
            idents.append(
                Identity(
                    pid=pid,
                    team=team,
                    role=role,
                    frames=np.asarray(frames)[order],
                    boxes=np.asarray(boxes).reshape(-1, 4)[order],
                    conf=np.asarray(conf)[order],
                    interp=np.asarray(interp)[order],
                    rows=np.asarray(rows)[order],
                    pieces=list(members),
                )
            )
        counts: dict = {}
        for ident in idents:
            key = ident.team if ident.role in (PLAYER, KEEPER) else ident.role
            counts[key] = counts.get(key, 0) + 1
        unsure = self._outfield_cap(idents, det) if s.outfield_cap else {}
        return LinkResult(
            idents, joins, refused, counts, defends_left, unsure, self._guesses(idents, pieces)
        )

    def _guesses(self, idents: list[Identity], pieces) -> dict:
        """Pid -> earlier pid, or None, for players who appear mid-window.

        Each earlier identity is offered to at most one newcomer, the one it
        fits best; a near tie names nobody.
        """
        s = self.s
        play = [i for i in idents if i.role in (PLAYER, KEEPER) and len(i.frames)]
        if not play:
            return {}
        start = min(int(i.frames[0]) for i in idents if len(i.frames))
        out, offers = {}, []
        for b in play:
            if b.frames[0] - start < s.guess_after_s * s.fps:
                continue
            first = pieces[b.pieces[0]]
            costs = sorted(
                (self._cost(pieces[a.pieces[-1]], first, self._occupancy), a.pid)
                for a in play
                if a.frames[-1] < b.frames[0]
                and b.frames[0] - a.frames[-1] <= s.guess_window_s * s.fps
            )
            costs = [x for x in costs if np.isfinite(x[0])]
            if not costs or costs[0][0] >= s.guess_doubt_cost:
                continue  # nobody seen before fits: somebody new
            out[b.pid] = None
            c, a = costs[0]
            margin = costs[1][0] - c if len(costs) > 1 else np.inf
            if (c < s.guess_sure_cost and margin > s.guess_sure_margin) or (
                c < s.guess_cost and margin > s.guess_margin
            ):
                offers.append((c, b.pid, a))
        taken = set()
        for c, b, a in sorted(offers):
            if a not in taken:
                out[b] = a
                taken.add(a)
        return out

    def _outfield_cap(self, idents: list[Identity], det: Detections) -> dict:
        """Frame -> pids beyond `outfield_cap` same-team outfield players there.

        Per frame, not per identity: a player is only over the cap while too
        many of their kit are in view, so greying a whole identity for it takes
        out real players with it (on fm01 it greyed black players to keep two
        navy bibs). Sureness is the identity's median lean toward its own kit,
        over the rows where its torso is clear; a navy bib over white leans
        less than a black shirt does, and a white shirt bent over in the shade
        hardly at all.
        """
        cap = self.s.outfield_cap
        a = self._assignment
        covered = _torso_covered(det) if a is not None and det.kit is not None else None
        sure = {}
        for ident in idents:
            if ident.role != PLAYER or ident.team not in (TEAM_A, TEAM_B):
                continue
            rows = ident.rows[ident.rows >= 0]
            lean = np.nan
            if covered is not None and len(rows):
                clear = rows[~covered[rows]]
                vals = a.lean(det.kit[clear if len(clear) else rows])
                if np.isfinite(vals).any():
                    lean = float(np.nanmedian(vals)) * (1.0 if ident.team == TEAM_B else -1.0)
            sure[ident.pid] = (lean if np.isfinite(lean) else 0.0, len(rows))
        by_frame: dict = {}
        for ident in idents:
            if ident.pid in sure:
                for f in ident.frames.tolist():
                    by_frame.setdefault(f, {}).setdefault(ident.team, []).append(ident.pid)
        unsure = {}
        for f, teams in by_frame.items():
            for pids in teams.values():
                if len(pids) > cap:
                    pids = sorted(pids, key=lambda p: sure[p], reverse=True)
                    unsure.setdefault(f, set()).update(pids[cap:])
        return unsure

    def _fill(self, a: _Piece, b: _Piece, det, frames, boxes, conf, interp, rows) -> None:
        """Straight-line feet across a short joined gap, boxes resized in step."""
        ba, bb = det.box[a.rows[-1]], det.box[b.rows[0]]
        pa, pb = a.pos[-1], b.pos[0]
        ha, hb = ba[3] - ba[1], bb[3] - bb[1]
        wa, wb = ba[2] - ba[0], bb[2] - bb[0]
        for f in range(a.last + 1, b.first):
            if not self.proj.has(f):
                continue
            w = (f - a.last) / (b.first - a.last)
            u, v = self.proj.to_px(f, (pa * (1 - w) + pb * w)[None])[0]
            h = ha * (1 - w) + hb * w
            bw = wa * (1 - w) + wb * w
            frames.append(f)
            boxes.append(np.array([u - bw / 2, v - h, u + bw / 2, v]))
            conf.append(0.0)
            interp.append(True)
            rows.append(-1)


def _weighted_median(x: np.ndarray, w: np.ndarray) -> float:
    order = np.argsort(x)
    c = np.cumsum(w[order])
    return float(x[order][np.searchsorted(c, c[-1] / 2.0)])
