"""Possession and team split on real footage, without a calibration.

This is the first genuinely coach-facing number the CV pipeline can produce
today: possession needs only "which team's player is nearest the ball", which
is answerable in pixels. Distance, speed and shape all wait on a fixed camera.

    python -m cv.experiments.possession_report "match.mp4" --start 30 --end 90
    python -m cv.experiments.possession_report "match.mp4" --save-frames
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from cv.ball import build_trajectory, candidates_from_detections
from cv.detector import PersonBallDetector
from cv.possession import build_states, summarise
from cv.teams import (
    TEAM_A, TEAM_B, UNKNOWN, assign_teams, separation, shirt_colour,
)

# Kits this close in Lab are hard to tell apart, and every team number derived
# from them should be treated as unreliable.
WEAK_SEPARATION = 25.0


def lab_to_bgr(lab) -> tuple[int, int, int]:
    patch = np.uint8([[[*lab]]])
    bgr = cv2.cvtColor(patch, cv2.COLOR_Lab2BGR)[0][0]
    return tuple(int(c) for c in bgr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='possession_report',
        description='Team split and possession from footage, no calibration needed.',
    )
    parser.add_argument('video', type=Path)
    parser.add_argument('--start', type=float, default=30.0)
    parser.add_argument('--end', type=float, default=90.0)
    parser.add_argument('--conf', type=float, default=0.25)
    parser.add_argument('--ball-conf', type=float, default=0.08,
                        help='the ball needs a far lower threshold than players')
    parser.add_argument('--imgsz', type=int, default=1280)
    parser.add_argument('--device', default=0)
    parser.add_argument('--name-a', default='Team A')
    parser.add_argument('--name-b', default='Team B')
    parser.add_argument('--save-frames', action='store_true',
                        help='write annotated frames for eyeballing the team split')
    args = parser.parse_args(argv)

    if not args.video.exists():
        print(f'error: video not found: {args.video}')
        return 1

    print(f'\n{args.video.name}  {args.start:.0f}s-{args.end:.0f}s')

    started = time.perf_counter()

    # One detector pass at the ball's lower threshold; players are filtered back
    # up afterwards. Running the model twice would double the cost for nothing.
    detector = PersonBallDetector(
        conf=args.ball_conf, imgsz=args.imgsz, device=args.device
    )

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print('error: could not open video')
        return 1

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(args.start * fps))
    total = int((args.end - args.start) * fps)

    frames = []
    for _ in range(total):
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()

    if not frames:
        print('error: no frames read')
        return 1

    frame_width = frames[0].shape[1]

    # ---- detect ----
    detections_by_frame: dict[int, list] = {}
    timestamps: dict[int, float] = {}
    for i in range(0, len(frames), 16):
        batch = frames[i:i + 16]
        for k, dets in enumerate(detector.detect_batch(batch)):
            idx = i + k
            detections_by_frame[idx] = dets
            timestamps[idx] = args.start + idx / fps

    # ---- ball ----
    trajectory = build_trajectory(
        candidates_from_detections(detections_by_frame, timestamps), frame_width
    )
    ball_by_frame = {p.frame_index: p.xy for p in trajectory.points}

    # ---- players ----
    # Detections are positional only here; identity comes from proximity within
    # a frame, which is all possession needs. Track ids would be nice for
    # per-player touches but fragment badly (see Phase 6), so they are not used.
    boxes_by_frame: dict[int, list] = {}
    samples: dict[int, list] = {}

    for idx, dets in detections_by_frame.items():
        people = [d for d in dets if d.label == 'person' and d.confidence >= args.conf]
        boxes = []
        for n, det in enumerate(people):
            pseudo_id = idx * 100 + n          # unique within the frame
            boxes.append((pseudo_id, det.xyxy))
            colour = shirt_colour(frames[idx], det.xyxy)
            if colour is not None:
                samples[pseudo_id] = [colour]
        boxes_by_frame[idx] = boxes

    if len(samples) < 4:
        print('error: too few player detections to identify teams')
        return 1

    assignment = assign_teams(samples)
    gap = separation(assignment)

    counts = assignment.counts
    print(f'\nplayer samples   {len(samples)}')
    print(f'  {args.name_a:<12} {counts.get(TEAM_A, 0)}')
    print(f'  {args.name_b:<12} {counts.get(TEAM_B, 0)}')
    print(f'  {"unassigned":<12} {counts.get(UNKNOWN, 0)}  (keepers, officials, odd kit)')

    if TEAM_A in assignment.centres:
        print(f'\nkit colours (BGR)')
        print(f'  {args.name_a:<12} {lab_to_bgr(assignment.centres[TEAM_A])}')
        print(f'  {args.name_b:<12} {lab_to_bgr(assignment.centres[TEAM_B])}')
    print(f'  separation   {gap:.0f}'
          + ('  <- too close to trust' if gap < WEAK_SEPARATION else ''))

    # ---- possession ----
    states = build_states(
        sorted(detections_by_frame), ball_by_frame, boxes_by_frame,
        assignment.team_of, timestamps,
    )
    summary = summarise(states)

    elapsed = time.perf_counter() - started
    window = args.end - args.start

    # How often anyone was actually close enough to the ball to hold it. This
    # is the number that decides whether the possession split above means
    # anything, so it is reported rather than buried.
    clear = sum(1 for s in states if s.team in (TEAM_A, TEAM_B))
    clear_share = clear / len(states) if states else 0.0

    heights = [
        h for h in (
            np.median([float(b[3]) - float(b[1]) for _, b in boxes])
            if boxes else 0
            for boxes in boxes_by_frame.values()
        ) if h > 0
    ]
    typical_height = float(np.median(heights)) if heights else 0.0

    print(f'\nball tracked     {trajectory.coverage(len(frames)):.0%} of frames')
    print(f'player size      {typical_height:.0f}px tall (possession radius '
          f'{typical_height * 1.6:.0f}px)')
    print(f'clear holder     {clear_share:.0%} of frames')
    print(f'possession       {summary.summary_line(args.name_a, args.name_b)}')
    print(f'\n{len(frames) / elapsed:.1f} frames/sec ({elapsed:.0f}s)')

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

    if args.save_frames:
        out_dir = Path('scratch_frames') / f'{args.video.stem}_possession'
        out_dir.mkdir(parents=True, exist_ok=True)
        colours = {
            TEAM_A: (60, 220, 255), TEAM_B: (255, 140, 60), UNKNOWN: (150, 150, 150),
        }

        for idx in list(sorted(boxes_by_frame))[::60]:
            img = frames[idx].copy()
            for pseudo_id, xyxy in boxes_by_frame[idx]:
                team = assignment.team_of(pseudo_id)
                x1, y1, x2, y2 = (int(v) for v in xyxy)
                cv2.rectangle(img, (x1, y1), (x2, y2), colours[team], 2)
            if idx in ball_by_frame:
                bx, by = (int(v) for v in ball_by_frame[idx])
                cv2.circle(img, (bx, by), 14, (0, 0, 255), 3)
            cv2.imwrite(str(out_dir / f'{idx:05d}.jpg'), img)

        print(f'annotated frames -> {out_dir}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
