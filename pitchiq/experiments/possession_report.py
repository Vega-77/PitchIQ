"""Possession and team split on real footage, without a calibration.

This is the first genuinely coach-facing number the CV pipeline can produce
today: possession needs only "which team's player is nearest the ball", which
is answerable in pixels. Distance, speed and shape all wait on a fixed camera.

    python -m pitchiq.experiments.possession_report "match.mp4" --start 30 --end 90
    python -m pitchiq.experiments.possession_report "match.mp4" --save-frames
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from pitchiq.ball import BallTrajectory, build_trajectory, candidates_from_detections
from pitchiq.detector import PersonBallDetector
from pitchiq.possession import (
    FrameState,
    PossessionSummary,
    build_states,
    summarise_possession,
)
from pitchiq.teams import (
    TEAM_A,
    TEAM_B,
    UNKNOWN,
    TeamAssignment,
    assign_teams,
    separation,
    shirt_colour,
)

# Kits this close in Lab are hard to tell apart, and every team number derived
# from them should be treated as unreliable.
WEAK_SEPARATION = 25.0


def lab_to_bgr(lab) -> tuple[int, int, int]:
    patch = np.uint8([[[*lab]]])
    bgr = cv2.cvtColor(patch, cv2.COLOR_Lab2BGR)[0][0]
    return tuple(int(c) for c in bgr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.possession_report',
        description='Team split and possession from footage, no calibration needed.',
    )
    parser.add_argument('video', type=Path)
    parser.add_argument('--start', type=float, default=30.0)
    parser.add_argument('--end', type=float, default=90.0)
    parser.add_argument('--conf', type=float, default=0.25)
    parser.add_argument(
        '--ball-conf',
        type=float,
        default=0.08,
        help='the ball needs a far lower threshold than players',
    )
    parser.add_argument('--imgsz', type=int, default=1280)
    parser.add_argument('--device', default=0)
    parser.add_argument('--name-a', default='Team A')
    parser.add_argument('--name-b', default='Team B')
    parser.add_argument(
        '--save-frames',
        action='store_true',
        help='write annotated frames for eyeballing the team split',
    )
    return parser


@dataclass
class PossessionRun:
    """What one run measured: enough to print it, or to draw it on the frames."""

    frames: list[np.ndarray]
    trajectory: BallTrajectory
    ball_by_frame: dict[int, tuple[float, float]]
    boxes_by_frame: dict[int, list]
    samples: dict[int, list]
    assignment: TeamAssignment
    states: list[FrameState]
    summary: PossessionSummary

    @property
    def clear_share(self) -> float:
        """How often anyone was actually close enough to the ball to hold it.

        This is the number that decides whether the possession split means
        anything, so it is reported rather than buried.
        """
        clear = sum(1 for s in self.states if s.team in (TEAM_A, TEAM_B))
        return clear / len(self.states) if self.states else 0.0

    @property
    def typical_height(self) -> float:
        """The median player's height in pixels, over frames that had players."""
        heights = [
            h
            for h in (
                np.median([float(b[3]) - float(b[1]) for _, b in boxes]) if boxes else 0
                for boxes in self.boxes_by_frame.values()
            )
            if h > 0
        ]
        return float(np.median(heights)) if heights else 0.0


def read_frames(video: Path, start_s: float, end_s: float) -> tuple[list[np.ndarray], float]:
    """The window's frames and the video's frame rate. Raises ValueError if none."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError('could not open video')

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))
    total = int((end_s - start_s) * fps)

    frames = []
    for _ in range(total):
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()

    if not frames:
        raise ValueError('no frames read')
    return frames, fps


def measure(
    frames: list[np.ndarray],
    fps: float,
    start_s: float,
    detector: PersonBallDetector,
    conf: float,
) -> PossessionRun:
    """Detect, find the ball, split the teams and take possession over `frames`.

    `detector` runs once at the ball's lower threshold; players are filtered
    back up to `conf` afterwards. Raises ValueError when there are too few
    players to split into teams.
    """
    frame_width = frames[0].shape[1]

    detections_by_frame: dict[int, list] = {}
    timestamps: dict[int, float] = {}
    for i in range(0, len(frames), 16):
        batch = frames[i : i + 16]
        for k, dets in enumerate(detector.detect_batch(batch)):
            idx = i + k
            detections_by_frame[idx] = dets
            timestamps[idx] = start_s + idx / fps

    trajectory = build_trajectory(
        candidates_from_detections(detections_by_frame, timestamps), frame_width
    )
    ball_by_frame = {p.frame_index: p.xy for p in trajectory.points}

    # Detections are positional only here; identity comes from proximity within
    # a frame, which is all possession needs. Track ids would be nice for
    # per-player touches but fragment badly, so they are not used.
    boxes_by_frame: dict[int, list] = {}
    samples: dict[int, list] = {}

    for idx, dets in detections_by_frame.items():
        people = [d for d in dets if d.label == 'person' and d.confidence >= conf]
        boxes = []
        for n, det in enumerate(people):
            pseudo_id = idx * 100 + n  # unique within the frame
            boxes.append((pseudo_id, det.xyxy))
            colour = shirt_colour(frames[idx], det.xyxy)
            if colour is not None:
                samples[pseudo_id] = [colour]
        boxes_by_frame[idx] = boxes

    if len(samples) < 4:
        raise ValueError('too few player detections to identify teams')

    assignment = assign_teams(samples)
    states = build_states(
        sorted(detections_by_frame),
        ball_by_frame,
        boxes_by_frame,
        assignment.team_of,
        timestamps,
    )
    return PossessionRun(
        frames=frames,
        trajectory=trajectory,
        ball_by_frame=ball_by_frame,
        boxes_by_frame=boxes_by_frame,
        samples=samples,
        assignment=assignment,
        states=states,
        summary=summarise_possession(states),
    )


def print_run(run: PossessionRun, name_a: str, name_b: str, elapsed: float) -> None:
    """The team split, the possession split, and how far to trust either."""
    assignment = run.assignment
    gap = separation(assignment)

    counts = assignment.counts
    print(f'\nplayer samples   {len(run.samples)}')
    print(f'  {name_a:<12} {counts.get(TEAM_A, 0)}')
    print(f'  {name_b:<12} {counts.get(TEAM_B, 0)}')
    print(f'  {"unassigned":<12} {counts.get(UNKNOWN, 0)}  (keepers, officials, odd kit)')

    if TEAM_A in assignment.centres:
        print('\nkit colours (BGR)')
        print(f'  {name_a:<12} {lab_to_bgr(assignment.centres[TEAM_A])}')
        print(f'  {name_b:<12} {lab_to_bgr(assignment.centres[TEAM_B])}')
    print(
        f'  separation   {gap:.0f}' + ('  <- too close to trust' if gap < WEAK_SEPARATION else '')
    )

    clear_share = run.clear_share
    typical_height = run.typical_height

    print(f'\nball tracked     {run.trajectory.coverage(len(run.frames)):.0%} of frames')
    print(
        f'player size      {typical_height:.0f}px tall (possession radius '
        f'{typical_height * 1.6:.0f}px)'
    )
    print(f'clear holder     {clear_share:.0%} of frames')
    print(f'possession       {run.summary.summary_line(name_a, name_b)}')
    print(f'\n{len(run.frames) / elapsed:.1f} frames/sec ({elapsed:.0f}s)')

    if gap < WEAK_SEPARATION:
        print(
            '\nThe two kits are too close in colour to tell apart reliably.\n'
            'Treat the split above as a coin flip, not a measurement.'
        )

    if clear_share < 0.25:
        print(
            f'\nUNRELIABLE: a holder was identifiable in only {clear_share:.0%} of\n'
            'frames, so the split above is drawn from a small and probably\n'
            'unrepresentative slice of play.'
        )
        if typical_height < 80:
            print(
                f'Players are {typical_height:.0f}px tall here. At that size the ball is a\n'
                'few pixels wide and most "ball" detections are noise, so it rarely\n'
                'lands near a real player. Higher-resolution footage of a tighter\n'
                'view is the fix, not a bigger radius.'
            )


def save_annotated(run: PossessionRun, out_dir: Path) -> None:
    """Every 60th frame with team boxes and the ball, for eyeballing the split."""
    out_dir.mkdir(parents=True, exist_ok=True)
    colours = {
        TEAM_A: (60, 220, 255),
        TEAM_B: (255, 140, 60),
        UNKNOWN: (150, 150, 150),
    }

    for idx in list(sorted(run.boxes_by_frame))[::60]:
        img = run.frames[idx].copy()
        for pseudo_id, xyxy in run.boxes_by_frame[idx]:
            team = run.assignment.team_of(pseudo_id)
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            cv2.rectangle(img, (x1, y1), (x2, y2), colours[team], 2)
        if idx in run.ball_by_frame:
            bx, by = (int(v) for v in run.ball_by_frame[idx])
            cv2.circle(img, (bx, by), 14, (0, 0, 255), 3)
        cv2.imwrite(str(out_dir / f'{idx:05d}.jpg'), img)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.video.exists():
        print(f'error: video not found: {args.video}')
        return 1

    print(f'\n{args.video.name}  {args.start:.0f}s-{args.end:.0f}s')

    started = time.perf_counter()
    detector = PersonBallDetector(conf=args.ball_conf, imgsz=args.imgsz, device=args.device)
    try:
        frames, fps = read_frames(args.video, args.start, args.end)
        run = measure(frames, fps, args.start, detector, args.conf)
    except ValueError as err:
        print(f'error: {err}')
        return 1
    elapsed = time.perf_counter() - started

    print_run(run, args.name_a, args.name_b, elapsed)

    if args.save_frames:
        out_dir = Path('scratch_frames') / f'{args.video.stem}_possession'
        save_annotated(run, out_dir)
        print(f'annotated frames -> {out_dir}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
