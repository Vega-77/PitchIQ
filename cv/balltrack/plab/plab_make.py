"""Player lab sheets: 40 keyframes of fr00 with the shipped tracker's suggestions.

Runs the pipeline's own PlayerTracker (YOLOv8n @1280, BoT-SORT) on every frame
of the dev window so its ids are what the pipeline would really produce, then
keeps every 15th frame as a keyframe. Each box carries the tracker id and a
team guess from assign_teams, so the lab can prefill "same id -> same label".

    python plab_make.py OUTDIR [lo] [n] [step]
"""

import json
import os
import sys

import cv2
import numpy as np

S2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(S2))
sys.path.insert(0, REPO)
sys.path.insert(0, S2)

import feat4  # noqa: E402
from cv.tracking import PlayerTracker  # noqa: E402
from cv.teams import assign_teams, kit_sample  # noqa: E402

OUT = sys.argv[1]
LO = int(sys.argv[2]) if len(sys.argv) > 2 else 136170
N = int(sys.argv[3]) if len(sys.argv) > 3 else 40
STEP = int(sys.argv[4]) if len(sys.argv) > 4 else 15
HI = LO + N * STEP  # exclusive

os.makedirs(os.path.join(OUT, 'frames'), exist_ok=True)
cap = cv2.VideoCapture(feat4.VIDEO)
fps = cap.get(cv2.CAP_PROP_FPS)
cap.set(cv2.CAP_PROP_POS_FRAMES, LO)
tr = PlayerTracker(imgsz=1280)
tracks = {}
colours = {}
keys = {}
for f in range(LO, HI):
    ok, fr = cap.read()
    assert ok, f
    tr._ingest(fr, f, f / fps, tracks)
    for t in tracks.values():
        b = t.boxes[-1]
        if b.frame_index == f and b.label == 'person':
            s = kit_sample(fr, b.xyxy)
            if s is not None:
                colours.setdefault(t.track_id, []).append(s)
    if (f - LO) % STEP == 0:
        k = (f - LO) // STEP
        cv2.imwrite(
            os.path.join(OUT, 'frames', 'k%02d.jpg' % k), fr, [cv2.IMWRITE_JPEG_QUALITY, 82]
        )
        keys[f] = k
    if (f - LO) % 100 == 0:
        print(f, len(tracks), flush=True)

team = assign_teams(colours)
H, W = fr.shape[:2]
sheets = []
for f, k in sorted(keys.items()):
    boxes = []
    for t in tracks.values():
        if t.label != 'person':
            continue
        for b in t.boxes:
            if b.frame_index == f:
                try:
                    tm = team.team_of(t.track_id)
                except Exception:
                    tm = None
                boxes.append(
                    {
                        'tid': t.track_id,
                        'c': round(b.confidence, 2),
                        'team': tm,
                        'xyxy': [round(v, 1) for v in b.xyxy],
                    }
                )
    sheets.append({'k': k, 'f': f, 't': round(f / fps, 3), 'boxes': boxes})

life = {
    tid: [t.boxes[0].frame_index, t.boxes[-1].frame_index, len(t.boxes)]
    for tid, t in tracks.items()
    if t.label == 'person'
}
meta = {
    'video': os.path.basename(feat4.VIDEO),
    'fps': fps,
    'w': W,
    'h': H,
    'lo': LO,
    'hi': HI - 1,
    'step': STEP,
    'tracker': 'PlayerTracker yolov8n imgsz1280 botsort',
    'life': life,
    'sheets': sheets,
}
json.dump(meta, open(os.path.join(OUT, 'sheets.json'), 'w'))
print(
    'tracks',
    len(life),
    'keyframes',
    len(sheets),
    'boxes/kf',
    np.mean([len(s['boxes']) for s in sheets]),
    'teams',
    team.counts,
    team.axis,
    round(team.score, 2),
)
