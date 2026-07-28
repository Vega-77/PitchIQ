"""Turn clicked landmarks into a calibration, and say how good it is.

    python -m cv.experiments.calibrate calibration-points.json
    python -m cv.experiments.calibrate points.json --out cv/calibrations/home.json

Reports both reprojection error (optimistic — it is measured on the very points
that were fitted) and leave-one-out error (honest, and what actually catches a
mis-clicked landmark).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cv.calibration import Calibration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calibrate",
        description="Fit a pixel->pitch homography from exported landmark clicks.",
    )
    parser.add_argument("points", type=Path, help="JSON from the browser picker")
    parser.add_argument("--out", type=Path, default=None, help="where to save")
    parser.add_argument("--length", type=float, default=None, help="override pitch length (m)")
    parser.add_argument("--width", type=float, default=None, help="override pitch width (m)")
    args = parser.parse_args(argv)

    if not args.points.exists():
        print(f"error: {args.points} not found")
        return 1

    pitch = None
    if args.length or args.width:
        from cv.pitch import Pitch

        pitch = Pitch(length_m=args.length or 105.0, width_m=args.width or 68.0)

    try:
        calib = Calibration.from_picker_export(args.points, pitch)
    except (ValueError, KeyError) as err:
        print(f"error: {err}")
        return 1

    print(f"\npitch      {calib.pitch.length_m:.1f} x {calib.pitch.width_m:.1f} m")
    print(f"points     {len(calib.correspondences)}")
    if calib.image_size:
        print(f"frame      {calib.image_size[0]}x{calib.image_size[1]}")

    fit_err = calib.error()
    print(f"\nreprojection  {fit_err.summary()}")

    holdout = calib.holdout_error()
    if holdout is None:
        print(
            "leave-one-out  needs 5+ points\n"
            "               With exactly 4 the fit is exact by construction, so the\n"
            "               reprojection number above proves nothing. Add a point."
        )
    else:
        print(f"leave-one-out {holdout.summary()}")

    problems = calib.sanity_check()
    if problems:
        print("\nproblems:")
        for p in problems:
            print(f"  - {p}")

    worst = calib.worst_landmarks(3)
    if worst and (holdout and not holdout.is_usable or problems):
        print("\nmost suspect points:")
        for name, err in worst:
            print(f"  {err:6.2f}m  {name}")

    good = (holdout or fit_err).is_usable and not problems
    print(f"\n{'OK' if good else 'NEEDS WORK'}")

    if not good:
        print(
            "\nUsually one landmark is misplaced or mislabelled. Re-open the picker,\n"
            "check the yellow outline sits on the painted lines, and fix the point\n"
            "listed above."
        )

    out = args.out or Path("cv/calibrations") / f"{args.points.stem}.calib.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    calib.save(out)
    print(f"\nsaved -> {out}")
    return 0 if good else 2


if __name__ == "__main__":
    raise SystemExit(main())
