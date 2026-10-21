# balltrack/evaluation/

The test harness: every tracker version against every labelled test.

- `mkpicks_one.py <tag>` makes the shipped tracker's picks
  (`p2d/cls_B/<tag>.npz`); `hybrid.py` adds the TAPNext bridges
  (`p2d/hyb90/` is Hybrid 90).
- `mk3d.py <ver> <tag>` runs the 3D chain for one 2D version in `../h3d_<ver>/`.
- `score2d.py` and `score3d.py` write `results/2d.json` and `results/3d.json`;
  `build_report.py` turns them into `report.html`.
- Label sets, one packed file each: `findball/labels.jsonl` (Find the Ball),
  `fixlab/fixes.jsonl` (Fix Lab), `passbench/truth.jsonl` (Pass Truth Bench,
  with its page). The Ball Truth marks are in `../ground_truth/`.
