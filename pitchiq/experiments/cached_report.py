"""A match report for a panned half, from the person detections already on disk.

    python -m pitchiq.experiments.cached_report half wb1 --ours lighter --attacks-first left --out pipe_wb1.json
    python -m pitchiq.experiments.cached_report half wb1 ... --seconds 60      (a short test window)
    python -m pitchiq.experiments.cached_report half wb1 ... --tag-log pitchiq-log-....json
    python -m pitchiq.experiments.cached_report merge pipe_wb1.json pipe_wb2.json --out pipe_wb.json

`analyse_match` runs YOLO itself, at about half real time: an hour and a half a
half. The player lab has already run the same model (YOLOv8n at 1280, people
down to conf 0.05) over every frame of each half it touched, into
`plab/out/<tag>_dets.npz`. `CachedDetector` hands those rows back in place of
the model, so the rest of the pipeline (BoT-SORT, kit colours, the ball from
`pitchiq/balltrack`, possession, events, the identity merge) runs unchanged on
the decoded frames. The cached rows were made at conf 0.05 and the pass asks for
0.08 (`AnalysisOptions.ball_conf`), so the rows below that are dropped.

The cache is indexed by absolute frame and the detector is handed frames only,
so the window has to start exactly where the seek lands. `half` checks the
seek first and refuses if it would land anywhere else.

`merge` joins two halves into the one report the site keeps per match. Counts
are summed, accuracy is recomputed from the sums, possession and territory are
weighted by each half's length, and track, cluster and event lists are
concatenated with the second half's ids moved clear of the first's. The same
person in both halves is two clusters: nothing here can join them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from pitchiq.cli.event_report import load_tag_log, load_video_timing
from pitchiq.detector import CLASS_PERSON
from pitchiq.events import MIN_PRESSING_ACTIONS
from pitchiq.experiments.player_lab import _paths
from pitchiq.panning_camera import PannedCamera
from pitchiq.pipeline import AnalysisOptions, MatchContext, analyse_match
from pitchiq.pitch import MatchOrientation

REPO = Path(__file__).resolve().parents[2]
HALVES = REPO / 'pitchiq' / 'balltrack' / 'halves.json'
DOWNLOADS = Path.home() / 'Downloads'


class CachedDetector:
    """`detect_batch_raw` from `plab/out/<tag>_dets.npz` instead of the model."""

    def __init__(self, tag: str, first_frame: int, min_conf: float = 0.08):
        z = np.load(_paths(tag)['dets'])
        self.lo, self.n = int(z['lo']), int(z['n'])
        self.shape = (int(z['h']), int(z['w']))
        frame, box, conf = z['frame'], z['box'].astype(np.float32), z['conf'].astype(np.float32)
        keep = conf >= min_conf
        frame, box, conf = frame[keep], box[keep], conf[keep]
        order = np.argsort(frame, kind='stable')
        self.frame, self.box, self.conf = frame[order], box[order], conf[order]
        self.next = int(first_frame)
        if not self.lo <= self.next < self.lo + self.n:
            raise ValueError(
                f'{tag}: frame {first_frame} is outside the cache {self.lo}..{self.lo + self.n - 1}'
            )

    def rows(self, f: int) -> np.ndarray:
        a, b = np.searchsorted(self.frame, [f, f + 1])
        out = np.zeros((b - a, 6), np.float32)
        out[:, :4], out[:, 4], out[:, 5] = self.box[a:b], self.conf[a:b], CLASS_PERSON
        return out

    def detect_batch_raw(self, frames):
        from ultralytics.engine.results import Boxes

        out = []
        for _ in frames:
            if self.next >= self.lo + self.n:
                raise RuntimeError(f'frame {self.next} is past the end of the cache')
            out.append(Boxes(self.rows(self.next), self.shape))
            self.next += 1
        return out


def _half(tag: str) -> tuple[Path, int, int]:
    """The video and the half's first and last frame, from halves.json."""
    game, h = tag[:-1], int(tag[-1])
    entry = json.loads(HALVES.read_text(encoding='utf-8'))[game]
    span = next(v for k, v in entry.items() if k.startswith(f'H{h}'))
    return DOWNLOADS / entry['video'], int(span[2]), int(span[3])


def run_half(args) -> int:
    video, lo, hi = _half(args.tag)
    if args.seconds:
        hi = min(hi, lo + int(args.seconds * 30) - 1)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    # The pipeline seeks to int(start_s * fps); half a frame in keeps float
    # rounding from landing it one early.
    start_s, end_s = (lo + 0.5) / fps, (hi + 1.5) / fps
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))
    landed = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
    cap.release()
    if landed != lo:
        print(f'seek to {lo} lands on {landed}; the cache would be out of step', file=sys.stderr)
        return 2

    detector = CachedDetector(args.tag, lo)
    period = 'first_half' if args.tag.endswith('1') else 'second_half'
    timing = load_video_timing(args.tag_log)
    context = MatchContext(
        orientation=MatchOrientation(home_attacks_first_half=args.attacks_first),
        period=period,
        ours_kit=args.ours,
        tag_log=load_tag_log(args.tag_log),
        video_offset_s=timing.get('videoOffsetS', 0.0),
        second_half_video_s=timing.get('secondHalfVideoS'),
    )
    t0 = time.time()
    report = analyse_match(
        video,
        panned=PannedCamera.from_tracker(args.tag),
        start_s=start_s,
        end_s=end_s,
        options=AnalysisOptions(),
        context=context,
        detector=detector,
    )
    if detector.next != hi + 1:
        print(f'detector stopped at {detector.next}, expected {hi + 1}', file=sys.stderr)
        return 1
    data = report.to_json(window={'start_s': round(start_s, 3), 'end_s': round(end_s, 3)})
    data['source'] = args.tag
    args.out.write_text(json.dumps(data), encoding='utf-8')
    print(report.summary())
    print(
        f'{hi - lo + 1} frames in {time.time() - t0:.0f} s; attacking ends {report.attacking_ends}'
    )
    return 0


# ---------------------------------------------------------------- merging

COUNTS = (
    'touches', 'passes_attempted', 'passes_completed', 'progressive_passes',
    'final_third_entries', 'box_entries', 'switches', 'crosses', 'carries', 'shots',
    'shots_on_target', 'goals', 'xg', 'tackles', 'interceptions', 'recoveries', 'duels',
)  # fmt: skip
QUALITY_COUNTS = (
    'tracks', 'clusters', 'touches', 'unseen_spans', 'excluded_tracks', 'flagged_officials', 'no_ball_s',
)  # fmt: skip


def _add(a, b):
    """Sum two JSON values of the same shape; None only when both are."""
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: _add(a.get(k), b.get(k)) for k in {**a, **b}}
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(a + b, 4)
    return a


def _wmean(values, weights):
    """Weighted mean of numbers or nested dicts of numbers, over the halves that have one."""
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None and w]
    if not pairs:
        return None
    if all(isinstance(v, dict) for v, _ in pairs):
        keys = {k for v, _ in pairs for k in v}
        return {k: _wmean([v.get(k) for v, _ in pairs], [w for _, w in pairs]) for k in keys}
    if all(isinstance(v, (int, float)) for v, _ in pairs):
        total = sum(w for _, w in pairs)
        return round(sum(v * w for v, w in pairs) / total, 4)
    return pairs[0][0]


def _ppda(halves: list[dict], team: str):
    """Opponent passes allowed per defensive action, from the pressing blocks' own counts."""
    allowed = actions = 0
    for h in halves:
        for seg in h['teams'][team].get('pressing_segments') or []:
            allowed += seg.get('allowed') or 0
            actions += seg.get('actions') or 0
    return round(allowed / actions, 2) if actions >= MIN_PRESSING_ACTIONS else None


def merge(halves: list[dict], min_minutes: float = 0.0) -> dict:
    """One report for the match from its halves' reports, first half first."""
    durations = [h.get('duration_s') or 0.0 for h in halves]
    out = dict(halves[0])
    out['source'] = '+'.join(h['source'] for h in halves)
    out['window'] = {
        'start_s': halves[0]['window'].get('start_s'),
        'end_s': halves[-1]['window'].get('end_s'),
    }
    out['duration_s'] = round(sum(durations), 1)
    out['processing_s'] = round(sum(h.get('processing_s') or 0.0 for h in halves), 1)
    out['timing'] = None
    out['period'] = None
    out['period_source'] = None
    out['calibration_error_m'] = None
    # Shares and rates weighted by length; counts summed.
    quality = _wmean([h.get('quality') for h in halves], durations) or {}
    for key in QUALITY_COUNTS:
        quality[key] = None
        for h in halves:
            quality[key] = _add(quality[key], (h.get('quality') or {}).get(key))
    quality['blind'] = None
    out['quality'] = quality
    out['warnings'] = [f'{h["source"]}: {w}' for h in halves for w in h.get('warnings') or []]
    out['trustworthy'] = all(h.get('trustworthy') for h in halves)

    teams = {}
    for team in halves[0]['teams']:
        sides = [h['teams'][team] for h in halves]
        t = dict(sides[0])
        for key in COUNTS:
            t[key] = None
            for s in sides:
                t[key] = _add(t[key], s.get(key))
        for key in (
            'passes_by_length',
            'passes_by_direction',
            'phase_of_play',
            'turnovers_by_third',
        ):
            t[key] = None
            for s in sides:
                t[key] = _add(t[key], s.get(key))
        t['pass_accuracy'] = (
            round(t['passes_completed'] / t['passes_attempted'], 3)
            if t.get('passes_attempted')
            else None
        )
        t['possession_pct'] = _wmean([s.get('possession_pct') for s in sides], durations)
        possessed = [(s.get('possession_pct') or 0) * d for s, d in zip(sides, durations)]
        t['territory'] = _wmean([s.get('territory') for s in sides], possessed)
        if t['territory'] is not None:
            t['territory']['seconds'] = round(
                sum((s.get('territory') or {}).get('seconds') or 0 for s in sides), 1
            )
        t['pressing_segments'] = [
            seg for s in sides for seg in s.get('pressing_segments') or []
        ] or None
        t['ppda'] = _ppda(halves, team)
        t['shot_map'] = [m for s in sides for m in s.get('shot_map') or []] or None
        t['shape_drift'] = None
        t['attacking_end'] = None
        teams[team] = t
    out['teams'] = teams

    # Second half's ids moved clear of the first's, so nothing in the two collides.
    tracks, clusters, keepers, participants, events = [], [], [], [], []
    shift_c = shift_t = 0
    for h in halves:

        def tr(ids, s=shift_t):
            return [i + s for i in ids]

        for c in h.get('clusters') or []:
            clusters.append(
                {
                    **c,
                    'cluster_id': c['cluster_id'] + shift_c,
                    'track_ids': tr(c.get('track_ids') or []),
                }
            )
        for t in h.get('tracks') or []:
            tracks.append(
                {
                    **t,
                    'cluster_id': t['cluster_id'] + shift_c,
                    'track_ids': tr(t.get('track_ids') or []),
                }
            )
        for k in h.get('keepers') or []:
            keepers.append({**k, 'track_ids': tr(k.get('track_ids') or [])})
        for p in h.get('participants') or []:
            p = dict(p)
            if isinstance(p.get('track_id'), int):
                p['track_id'] += shift_t
            participants.append(p)
        for e in h.get('events') or []:
            e = dict(e)
            for key in ('track_id', 'receiver_track_id', 'opponent_track_id'):
                if isinstance(e.get(key), int):
                    e[key] += shift_t
            events.append(e)
        ids_c = [c['cluster_id'] for c in h.get('clusters') or []]
        ids_t = [i for c in h.get('clusters') or [] for i in c.get('track_ids') or []]
        shift_c += (max(ids_c) + 1) if ids_c else 0
        shift_t += (max(ids_t) + 1) if ids_t else 0
    if min_minutes:
        # A whole match is thousands of fragments; the site's identity document
        # holds about a thousand. The short ones go: no person is a 20 s track.
        keep = {t['cluster_id'] for t in tracks if (t.get('minutes_tracked') or 0) >= min_minutes}
        out['warnings'].append(
            f'per-player rows: {len(keep)} of {len(tracks)} track clusters kept, the ones '
            f'tracked for {min_minutes:g} min or more; team figures count every one'
        )
        tracks = [t for t in tracks if t['cluster_id'] in keep]
        clusters = [c for c in clusters if c['cluster_id'] in keep]
    out.update(
        tracks=tracks, clusters=clusters, keepers=keepers, participants=participants, events=events
    )
    out['reconciliation'] = None
    return out


def run_merge(args) -> int:
    halves = [json.loads(p.read_text(encoding='utf-8')) for p in args.reports]
    args.out.write_text(json.dumps(merge(halves, args.min_minutes)), encoding='utf-8')
    print(f'wrote {args.out}')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m pitchiq.experiments.cached_report')
    sub = parser.add_subparsers(dest='cmd', required=True)
    h = sub.add_parser('half', help='one half, from cached detections')
    h.add_argument('tag')
    h.add_argument('--ours', required=True, choices=('lighter', 'darker'))
    h.add_argument(
        '--attacks-first',
        required=True,
        choices=('left', 'right'),
        help="the end our side attacks in the first half (map_stats' attacking_end)",
    )
    h.add_argument('--seconds', type=float, help='only the first N seconds, for a test')
    h.add_argument(
        '--tag-log', help="the coach page's log download (it carries the video sync too)"
    )
    h.add_argument('--out', type=Path, required=True)
    m = sub.add_parser('merge', help="join two halves' reports into one")
    m.add_argument('reports', type=Path, nargs='+')
    m.add_argument('--out', type=Path, required=True)
    m.add_argument(
        '--min-minutes',
        type=float,
        default=0.0,
        help='drop per-player rows tracked for less than this (1 fits the site)',
    )
    args = parser.parse_args(argv)
    return run_half(args) if args.cmd == 'half' else run_merge(args)


if __name__ == '__main__':
    raise SystemExit(main())
