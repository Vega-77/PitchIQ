"""Run the event chain over a clip and print what it found, timestamped.

    PitchIQHelper/.venv/Scripts/python.exe -m cv.experiments.event_report \
        --video "C:/Users/alexv/Videos/clip.mp4" --start 90 --end 105

This exists to be checked against the video, not to be believed on its own.
Every threshold in cv/touches.py and cv/events.py is a guess that has never
been compared to a human watching the same footage, and the only way to turn
those guesses into settings is to scrub to each timestamp below and mark it
right or wrong. Print it, watch it, count the errors — that is the precision
and recall figure this project does not yet have.

Two flags matter for that:

  --min-confidence   raise it to see only what the pipeline is sure of, which
                     is how you find where the threshold should sit
  --touches          show the raw touches with all four confidence components,
                     for when an event looks wrong and the question is which
                     half of the detector produced it

On footage from a camera that pans and zooms, expect close to nothing. That is
the honest outcome rather than a broken run: the ball is detected in a minority
of frames, and on the clips available the nearest player to a detected "ball"
is usually several player heights away, meaning most of those detections are
not the ball at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cv.events import CARRY, PASS, SHOT
from cv.pipeline import analyse_match
from cv.teams import TEAM_A, TEAM_B


def clock(seconds: float) -> str:
    return f'{int(seconds // 60):02d}:{seconds % 60:05.2f}'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--video', required=True)
    parser.add_argument('--calibration', default=None,
                        help='without it, shots and anything positional are skipped')
    parser.add_argument('--start', type=float, default=0.0)
    parser.add_argument('--end', type=float, default=None)
    parser.add_argument('--conf', type=float, default=0.25)
    parser.add_argument('--ball-conf', type=float, default=0.08)
    parser.add_argument('--imgsz', type=int, default=1280)
    parser.add_argument('--device', default='0')
    parser.add_argument('--stride', type=int, default=1)
    parser.add_argument('--sample-fps', type=float, default=None,
                        help='analyse at about this many frames a second')
    # No default on purpose. With a tagged log the kickoff taps answer this, and
    # a default of `first_half` is how a second half gets processed with the
    # whole pitch mirrored and no sign of it anywhere in the output.
    parser.add_argument('--period', default=None,
                        choices=['first_half', 'second_half'],
                        help='which half this clip is; taken from the tagged '
                             'log when one is supplied, and only needed to '
                             'override it or when there is no log')
    parser.add_argument('--us', default=None, choices=[TEAM_A, TEAM_B],
                        help='which colour cluster is our team; without it no '
                             'attacking direction is known and shots are skipped')
    parser.add_argument('--min-confidence', type=float, default=0.0)
    parser.add_argument('--touches', action='store_true',
                        help='list raw touches with their confidence components')
    parser.add_argument('--json', dest='json_path', default=None,
                        help='also write the full report JSON here')
    parser.add_argument('--tag-log', dest='tag_log', default=None,
                        help='the hand-tagged match log, downloaded from the '
                             'coach page. Without it every stoppage counts as '
                             'possession for whoever stood over the ball')
    parser.add_argument('--video-offset', dest='video_offset', type=float,
                        default=None,
                        help='seconds to add to a match clock reading to get '
                             'the position in this video — the same number the '
                             'coach types beside the video link. Read from the '
                             'downloaded log when it carries one')
    parser.add_argument('--second-half-video', dest='second_half_video',
                        type=float, default=None,
                        help='seconds into this video that the second half '
                             'kicks off. The tablet stops its clock for the '
                             'interval and the recording does not, so without '
                             'this every second-half timestamp is placed late '
                             'by the length of the break. Read from the '
                             'downloaded log when it carries one')
    return parser


def load_tag_log(path: str | None):
    """The log as `assets/db.js::listLog` returns it: a list of entries.

    Also accepts the whole object the coach page downloads, which wraps that
    list in `{'entries': [...]}` alongside the ids it came from — so a file can
    say which match it belongs to without the loader having to care.
    """
    if not path:
        return None

    data = json.loads(Path(path).read_text(encoding='utf-8'))
    entries = data.get('entries') if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f'{path} is not a match log')
    return entries


def load_video_timing(path: str | None) -> dict:
    """The clock numbers the coach page writes into the same download.

    Both of them are typed into the coach's video form and both are needed to
    place a second-half timestamp, so carrying them in the file beats asking
    whoever runs the pipeline to remember two numbers from a browser tab. A bare
    list of entries — the older shape, and what an export from anywhere else
    looks like — carries neither, and returns nothing rather than zeroes.
    """
    if not path:
        return {}

    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        return {}

    out = {}
    for key in ('videoOffsetS', 'secondHalfVideoS'):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
    return out


def _pick(*candidates):
    """The first thing that was actually supplied. The last is the default."""
    for value in candidates:
        if value is not None:
            return value
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timing = load_video_timing(args.tag_log)

    video = Path(args.video)
    if not video.exists():
        print(f'no such video: {video}', file=sys.stderr)
        return 2

    side_of_team = None
    if args.us:
        side_of_team = {
            args.us: 'us',
            TEAM_B if args.us == TEAM_A else TEAM_A: 'them',
        }

    report = analyse_match(
        video,
        calibration_path=args.calibration,
        start_s=args.start,
        end_s=args.end,
        conf=args.conf,
        ball_conf=args.ball_conf,
        imgsz=args.imgsz,
        device=args.device if args.device == 'cpu' else int(args.device),
        stride=args.stride,
        sample_fps=args.sample_fps,
        period=args.period,
        side_of_team=side_of_team,
        tag_log=load_tag_log(args.tag_log),
        # A flag beats the file, and the file beats nothing. Which is the right
        # precedence for a number that is right in the file until somebody
        # re-cuts the video and has not saved the new one yet.
        video_offset_s=_pick(args.video_offset, timing.get('videoOffsetS'), 0.0),
        second_half_video_s=_pick(
            args.second_half_video, timing.get('secondHalfVideoS'), None,
        ),
    )

    print()
    print(report.summary())
    print()

    if report.phases and report.phases.spans:
        print(f'  {len(report.phases.spans)} stoppages, '
              f'{report.phases.dead_s:.0f}s dead')
        print('  from      to        opened by       closed by')
        for span in report.phases.spans:
            print(f'  {clock(span.start_s)}  {clock(span.end_s)}  '
                  f'{span.opened_by:14s}  {span.closed_by or "(timed out)"}')
        print()

    if report.reconciliation is not None:
        rec = report.reconciliation
        goals = rec.counts('goal')
        print(f'  goals: {goals["agreed"]} agreed, {goals["cv_only"]} found '
              f'but never tagged, {goals["tag_only"]} tagged but never found')

        if rec.exits_checked:
            exits = rec.counts('exit')
            rate = rec.rate('exit')
            print(f'  ball out of play: {exits["agreed"]} agreed, '
                  f'{exits["cv_only"]} unseen by the tagger, '
                  f'{exits["tag_only"]} unseen here'
                  + (f'  ({rate:.0%} agreement)' if rate is not None else ''))
        else:
            print('  ball out of play: not checked (needs a calibration)')

        for entry in rec.disagreements('goal'):
            side = 'video only' if entry.status == 'cv_only' else 'tag only '
            print(f'  disagreement  {clock(entry.at_s)}  {side}')
        print()

    if args.touches and report.touches:
        print('  touches')
        print('  time      track  team      dist  prox  motn  obs   sep   score')
        for t in report.touches:
            c = t.components
            print(f'  {clock(t.timestamp_s)}  {t.track_id:5d}  {t.team:8s} '
                  f'{t.distance_ph:5.2f} {c.proximity:5.2f} {c.motion_change:5.2f} '
                  f'{c.observation:5.2f} {c.separation:5.2f} {t.confidence:6.2f}'
                  + ('' if t.observed else '   (inferred)'))
        print()

    events = [
        e for e in (report.events or [])
        if e.confidence >= args.min_confidence
    ]

    if not events:
        print('  no events above the confidence floor')
    else:
        print(f'  {len(events)} events')
        print('  time      type          team      track  detail')
        for event in events:
            print(f'  {clock(event.timestamp_s)}  {event.type:12s}  '
                  f'{event.team:8s}  {str(event.track_id):5s}  {_detail(event)}')

    if report.clusters:
        print()
        print(f'  {len(report.clusters)} player clusters, largest first')
        for cluster in report.clusters[:15]:
            print(f'    #{cluster.cluster_id:<3d} {cluster.team:8s} '
                  f'{len(cluster.track_ids):3d} tracks  '
                  f'{cluster.minutes_tracked * 60:5.1f}s  '
                  f'{cluster.sightings:5d} sightings')

    if args.json_path:
        window = {'start_s': args.start, 'end_s': args.end}
        Path(args.json_path).write_text(
            json.dumps(report.to_json(window=window), indent=2), encoding='utf-8'
        )
        print()
        print(f'  wrote {args.json_path}')

    return 0


def _detail(event) -> str:
    if event.type == PASS:
        bits = [event.outcome]
        if event.length_m is not None:
            bits.append(f'{event.length_m:.0f}m')
        if event.direction:
            bits.append(event.direction)
        if event.crossed_gap:
            bits.append('ACROSS AN UNSEEN SPAN')
        bits.extend(event.tags)
        return ', '.join(bits)
    if event.type == SHOT:
        bits = [event.outcome]
        if event.distance_to_goal_m is not None:
            bits.append(f'{event.distance_to_goal_m:.0f}m out')
        if event.xg is not None:
            bits.append(f'xG {event.xg:.2f}')
        if event.under_pressure:
            bits.append(f'{event.pressure_count} pressing')
        return ', '.join(bits)
    if event.type == CARRY:
        return f'{event.touches} touches'
    return ', '.join(event.tags) or ''


if __name__ == '__main__':
    raise SystemExit(main())
