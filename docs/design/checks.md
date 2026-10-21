# `pitchiq/checks.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Automatic checks: signs that a calibration or a top-down map is wrong, with no labels.

Each check returns plain numbers, so a run can be judged per game and per
stretch and the numbers compared run to run. None of them proves the map is
right; each one catches a kind of wrong that has happened.

Calibration
  * `feet_on_pitch`: the share of people's feet that the calibration puts on
    the pitch. People stand on it; a far touchline posed in mid-pitch throws
    the far half's players off it (East Brunswick eb02: 0.16 against 0.85-0.94
    on NB and WW). It does not catch a pitch drawn too big (eb01: 1.00 with the
    far line on the running track); `cal_overlay` by eye still does.
  * `far_near`: how tall the calibration makes far people against near ones,
    and the median height. Validated on EB frames where the answer is known:
    right calibrations (ebh1, ebh2, ww01, nbh1) give 0.94-1.02 and 1.72-1.79 m;
    eb01 (pitch too big) 0.67 and 1.44 m, eb02 (far line mid-pitch) 2.20 and
    2.82 m. Through EB's first half every 5-minute window stays 0.96-1.05;
    through WW's 0.91-1.14, with the height sliding 1.79 to 1.64 m (slight drift?).
    `cal_flags` turns it into a verdict.
  * `paint_on_lines`: the share of painted-line pixels within 1.5 m of the
    model's lines. It FAILED validation: eb01 scored 0.745 against ebh1's
    0.688 (a wrong pose still fits the paint it was fitted to), and through a
    half it swings 0.32-0.77 with where the camera points. Kept for looking
    at, never as a flag.

Tried and dropped
  * Kit mix inside one identity (the linker fixes an identity's team, so a
    "team flip" shows as its detections voting both kits). On the fm01/fr00
    labels it flags clean identities as often as merged ones (fm01, mix > 0.2:
    1 of 10 merges, 9 of 30 clean), so it is not a check.

Map physics (on a `topdown.TopDown`, positions in metres)
  * `speeds`: steps faster than a person runs, and one-frame jumps.
  * `crowding`: frames with more than eleven of a team on the pitch.
  * `still_outside`: players who stand outside the lines.
