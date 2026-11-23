"""Where a game's camera stood, against earlier games filmed at the same field.

    python -m pitchiq.experiments.venue_check check <h1 tag> <h2 tag> --out <dir>
    python -m pitchiq.experiments.venue_check book <tag> [<tag> ...]

Session step 3.8 (2026-11-22), the user's choice: the run matches the new game's
background against earlier games and flags a camera more than about 5 m from where it
stood there before (`run_checks.camera` decides; this module measures).

  pairs       SIFT keyframes of the two halves matched pairwise, as `pincal transfer`
              does between a game's halves: a pair is 60 homography inliers.
  same spot   3 or more pairs that a pure turn of the camera explains (rotation-only
              fit within 5 px): the tripod stood where it stood before, so the two
              calibrations must put it in the same place. A pair alone does not say
              "same field": the pitch paint is a plane, so two fields' lines match by a
              homography too. Woodbridge's away field "matched" the home field's games
              on 6-21 pairs, every one 24 px or more off a turn (most 100-600 px); the
              home games' pairs are all within 5 px (52 of 52 for ww1 and eb1).
              A camera set up somewhere else at a field is therefore not compared:
              nothing in the pictures tells its field from another with the same paint.
  position    each half's installed calibration (`<tag>_venue.npz`: camera C, pitch L
              and W, in centre-circle radii), as metres along the touchline from
              halfway, behind the near touchline and up. Measured from halfway, two
              calibrations of one field that disagree on its length still compare.

A half's keyframes are its pincal folder's (`pincal/<tag>/keys`, `keys.pkl`,
`solve.pkl`). A half calibrated another way gets a small copy of the same files in
`PitchIQ-data/venues/<tag>/` (`book`): its autocal samples' keyframes, read from the
video, with the chain's zooms. Which earlier games are compared: every game with
keyframes in either folder and an installed calibration, one half per camera (a game's
second half only when its camera stood more than 1 m from the first's).
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np

from pitchiq import autocal as A
from pitchiq.experiments import autocal_run as AR
from pitchiq.experiments import pincal as PC
from pitchiq.pins import M_PER_R

BOOK = Path(os.environ.get('PIQ_VENUES', PC.WORK.parent / 'venues'))
HOMOG = AR.HOMOG
MIN_INL = PC.MIN_INL  # inliers that make two keyframes a pair (as pincal transfer)
SPOT_PX = 5.0  # a pair a pure turn explains to within this (same spot <= 5, paint >= 24)
MIN_SPOT = 3  # such pairs that make the same spot (the home games have 3-63 a game pair)
MAX_KEYS = 16  # keyframes a half, spread over its view: 256 pairs, about 10 s a game
MOVED_M = 1.0  # a game's halves this far apart are two cameras


# ---------------------------------------------------------------- what is on disk


def camera(tag: str, homog: Path | None = None) -> dict | None:
    """A half's installed camera in metres, or None when it has no calibration.

    `along` is from halfway along the touchline (towards the goal on the camera's right),
    `back` behind the near touchline, `up` above the grass."""
    path = Path(homog or HOMOG) / f'{tag}_venue.npz'
    if not path.exists():
        return None
    z = np.load(path)
    C = np.asarray(z['C'], float) * M_PER_R
    L, W = float(z['L']) * M_PER_R, float(z['W']) * M_PER_R
    return dict(along=float(C[0] - L / 2), back=float(-C[1]), up=float(C[2]),
                length_m=L, width_m=W)  # fmt: skip


def apart(a: dict, b: dict) -> float:
    """Metres between two cameras."""
    return float(np.sqrt(sum((a[k] - b[k]) ** 2 for k in ('along', 'back', 'up'))))


def keyset(tag: str, roots: tuple[Path, ...] | None = None) -> dict | None:
    """A half's keyframes: {dir, keys, f}, from its pincal folder, else the venue book."""
    for root in roots or (PC.WORK, BOOK):
        d = Path(root) / tag
        if (d / 'keys.pkl').exists() and (d / 'solve.pkl').exists():
            keys = [int(k) for k in PC.load(d / 'keys.pkl')['keys']]
            f = np.asarray(PC.load(d / 'solve.pkl')['f'], float)
            return dict(dir=d, keys=keys, f=f)
    return None


def spread(keys: list[int], n: int = MAX_KEYS) -> list[int]:
    """At most n of a half's keyframes, evenly over the list (picked in view order)."""
    if len(keys) <= n:
        return list(keys)
    return [keys[i] for i in sorted(set(np.linspace(0, len(keys) - 1, n).round().astype(int)))]


def game_of(tag: str) -> str | None:
    """'wb1' -> 'wb'; None for a tag that is not a game's half (a clip, a test)."""
    return tag[:-1] if len(tag) >= 2 and tag[-1] in '12' and tag[:-1].isalpha() else None


def earlier(
    tag: str, roots: tuple[Path, ...] | None = None, homog: Path | None = None
) -> list[str]:
    """The halves to compare `tag` with: other games', one per camera."""
    own = game_of(tag)
    by_game: dict[str, list[str]] = {}
    for root in roots or (PC.WORK, BOOK):
        if not Path(root).exists():
            continue
        for d in sorted(Path(root).iterdir()):
            g = game_of(d.name)
            if g is None or g == own or d.name in by_game.get(g, []):
                continue
            if keyset(d.name, roots) is None or camera(d.name, homog) is None:
                continue
            by_game.setdefault(g, []).append(d.name)
    out = []
    for halves in by_game.values():
        kept = []
        for t in sorted(halves):
            if all(apart(camera(t, homog), camera(k, homog)) > MOVED_M for k in kept):
                kept.append(t)
        out += kept
    return out


# ---------------------------------------------------------------- the comparison


def _feats(ks: dict, k: int):
    z = np.load(ks['dir'] / 'keys' / ('k%05d.npz' % k))
    return z['xy'], z['d']


def compare(tag: str, other: str, roots: tuple[Path, ...] | None = None) -> dict:
    """Whether two halves were filmed from one spot: their keyframe pairs and how well
    a pure turn of the camera explains each."""
    a, b = keyset(tag, roots), keyset(other, roots)
    ka, kb = spread(a['keys']), spread(b['keys'])
    fa = {k: _feats(a, k) for k in ka}
    fb = {k: _feats(b, k) for k in kb}
    rng = np.random.default_rng(0)
    inliers, rms = [], []
    for x in ka:
        for y in kb:
            got = A.link_inliers(fa[x], fb[y])
            if got is None or len(got[0]) < MIN_INL:
                continue
            xa, xb = A.thin_link(got, rng=rng)
            Rr, f2 = A.relative_rotation(xa, xb, a['f'][x])
            pred = A.to_px(A.rays(xa, a['f'][x]) @ Rr.T, f2)
            inliers.append(len(got[0]))
            rms.append(float(np.sqrt(np.mean(np.sum((pred - xb) ** 2, 1)))))
    spot = sum(r <= SPOT_PX for r in rms)
    return dict(
        other=other,
        tried=len(ka) * len(kb),
        pairs=len(rms),
        spot_pairs=int(spot),
        inliers_p50=float(np.median(inliers)) if inliers else None,
        turn_px=[round(r, 1) for r in sorted(rms)],
        same_spot=bool(spot >= MIN_SPOT),
    )


def check(tag: str, roots: tuple[Path, ...] | None = None, homog: Path | None = None,
          log=print) -> dict:  # fmt: skip
    """`tag`'s camera against every earlier game's: the document run_checks.camera reads."""
    cam = camera(tag, homog)
    if cam is None or keyset(tag, roots) is None:
        raise SystemExit(f'{tag}: no installed calibration or keyframes to compare')
    out = []
    for other in earlier(tag, roots, homog):
        c = compare(tag, other, roots)
        c['camera'] = camera(other, homog)
        c['apart_m'] = apart(cam, c['camera'])
        out.append(c)
        log(
            '%s vs %s: %d of %d keyframe pairs, %d a pure turn: %s; calibrations %.1f m apart'
            % (tag, other, c['pairs'], c['tried'], c['spot_pairs'],
               'same spot' if c['same_spot'] else 'not the same spot', c['apart_m'])
        )  # fmt: skip
    return dict(tag=tag, camera=cam, compared=out)


def tags_to_check(tags: list[str], homog: Path | None = None) -> list[str]:
    """A game's first half, and its second when the camera stood elsewhere for it."""
    first = camera(tags[0], homog)
    return [t for i, t in enumerate(tags)
            if i == 0 or first is None or camera(t, homog) is None
            or apart(camera(t, homog), first) > MOVED_M]  # fmt: skip


# ---------------------------------------------------------------- the venue book


class _Renamed(pickle.Unpickler):
    """autocal pickles from before 2026-10-21 name the package `cv` (now `pitchiq`)."""

    def find_class(self, module, name):
        if module == 'cv' or module.startswith('cv.'):
            module = 'pitchiq' + module[2:]
        return super().find_class(module, name)


def _old(path: Path):
    with open(path, 'rb') as fh:
        return _Renamed(fh).load()


def book(tag: str, log=print) -> Path:
    """A keyframe set for a half without a pincal folder, from its autocal samples:
    PitchIQ-data/venues/<tag>/ (about 10 MB). Reads the video; changes nothing else."""
    import cv2

    d = BOOK / tag
    if (d / 'keys.pkl').exists():
        return d
    spec, Q = _old(AR.WORK / tag / 'spec.pkl'), _old(AR.WORK / tag / 'prep.pkl')
    os.environ['PIQ_VIDEO'] = spec['video']
    R, f = np.asarray(Q['R'], float), np.asarray(Q['f'], float)
    frames = [int(x) for x in Q['frames']]
    keys = spread([int(k) for k in PC.pick_keys(R, f)])
    (d / 'keys').mkdir(parents=True, exist_ok=True)
    cap = AR._video()
    sift = cv2.SIFT_create(nfeatures=3000)
    for k in keys:
        xy, desc = A.frame_features(AR._frame(cap, frames[k]), sift)
        np.savez(d / 'keys' / ('k%05d.npz' % k), xy=xy, d=desc)
    PC.save(d / 'solve.pkl', dict(f=f, frames=frames, source='autocal chain (prep.pkl)'))
    PC.save(d / 'keys.pkl', dict(keys=keys))
    log('%s: %d keyframes -> %s' % (tag, len(keys), d))
    return d


# ---------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.venue_check', description=__doc__.split('\n')[0]
    )
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help="a game's camera against earlier games at its field")
    c.add_argument('tags', nargs='+', help="the game's halves, first half first")
    c.add_argument('--out', type=Path, required=True, help='writes venue_<tag>.json here')
    b = sub.add_parser('book', help='keyframes for halves calibrated without pincal')
    b.add_argument('tags', nargs='+')
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == 'book':
        for t in a.tags:
            book(t)
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    for t in tags_to_check(a.tags):
        if keyset(t) is None:
            book(t)  # a half calibrated without pins: its autocal samples
        doc = check(t)
        (a.out / f'venue_{t}.json').write_text(json.dumps(doc, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
