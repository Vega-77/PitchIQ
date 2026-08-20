"""Turn clicked landmarks into a calibration, and say how good it is.

    python -m cv.experiments.calibrate calibration-points.json
    python -m cv.experiments.calibrate points.json --out cv/calibrations/home.json
    python -m cv.experiments.calibrate points.json --frame frame.png --refine
    python -m cv.experiments.calibrate points.json --frame frame.png --lens

Reports both reprojection error (optimistic — it is measured on the very points
that were fitted) and leave-one-out error (honest, and what actually catches a
mis-clicked landmark).

Both of those are measured on the human's own clicks, and neither can see a
mistake the human made consistently. `--frame` is the only argument here that
brings in evidence nobody supplied: the painted lines in a frame of the actual
pitch, found by `cv.lines` and compared against the model.

`--lens` reads that same paint for a different question. A homography can
only describe a lens that keeps straight lines straight, so on a wide-angle
camera every landmark can be clicked perfectly and the fit still be
unusable. Painted lines are straight by the Laws, which makes any bow in
them the lens and not the clicking — see `cv.distortion`. It is off by
default because a frame that cannot answer must not be made to guess.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
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
    parser.add_argument(
        "--frame", type=Path, default=None,
        help="a frame of the pitch, to grade the calibration against its paint",
    )
    parser.add_argument(
        "--refine", action="store_true",
        help="nudge the calibration onto the paint, and save that instead",
    )
    parser.add_argument(
        "--overlay", type=Path, default=None,
        help="write the frame with the pitch model drawn over it",
    )
    parser.add_argument(
        "--lens", action="store_true",
        help="measure lens distortion from the paint and fit through it",
    )
    args = parser.parse_args(argv)

    if (args.refine or args.overlay or args.lens) and args.frame is None:
        parser.error("--refine, --overlay and --lens need a --frame to work from")

    if not args.points.exists():
        print(f"error: {args.points} not found")
        return 1

    if args.frame is not None and not args.frame.exists():
        print(f"error: {args.frame} not found")
        return 1

    pitch = None
    if args.length or args.width:
        # Overriding the size must not throw away the markings. A file whose
        # picker measured a 15m box carries that measurement in the same block
        # as the length, and building a fresh `Pitch` here would quietly put
        # the Laws back — refitting the same clicks against a field that does
        # not exist, from a command that only meant to correct the length.
        from cv.pitch import Pitch

        try:
            dims = (json.loads(args.points.read_text(encoding="utf-8"))
                    .get("pitch") or {})
        except (OSError, ValueError):
            dims = {}
        pitch = replace(
            Pitch.from_mapping(dims),
            length_m=args.length or float(dims.get("length_m", 105.0)),
            width_m=args.width or float(dims.get("width_m", 68.0)),
        )

    lens = _estimate_lens(args) if args.lens else None

    try:
        calib = Calibration.from_picker_export(args.points, pitch, lens)
    except (ValueError, KeyError) as err:
        print(f"error: {err}")
        return 1

    print(f"\npitch      {calib.pitch.length_m:.1f} x {calib.pitch.width_m:.1f} m")
    if not calib.pitch.markings_are_standard:
        # Said out loud because it changes what every number below means. These
        # come from the picker, where the coach measured them from their own
        # clicks and pressed the button accepting them; nothing here re-derives
        # them, and nothing here should.
        print("markings   not to the Laws, and fitted as measured:")
        for label, note in _non_standard_marks(calib.pitch):
            print(f"           {label:<20} {note}")
    print(f"points     {len(calib.correspondences)}")
    if calib.image_size:
        print(f"frame      {calib.image_size[0]}x{calib.image_size[1]}")
    if calib.lens is not None:
        print(f"lens       k1 {calib.lens.k1:+.4f}")

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

    if args.frame is not None:
        calib, code = _check_against_paint(calib, args)
        if code is not None:
            return code
        fit_err = calib.error()
        holdout = calib.holdout_error()

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
        # The suspect list above is earned here in a way it is not in the
        # browser: `Calibration.fit` uses RANSAC for five or more points, which
        # rejects an outlier and fits the rest exactly, so a single bad click
        # ends up carrying the whole residual. Measured on synthetic cameras at
        # eight or more points, the worst point held 100% of the error in every
        # trial. The picker page solves normal equations instead and spreads a
        # bad point across all of them, which is why it can only list causes.
        #
        # What neither can do is name the cause. A wide-angle lens produces
        # about 1.1m of error at k1 = -0.03 with every point clicked perfectly,
        # and re-clicking will never fix it.
        print(
            "\nFour things do this and these numbers cannot separate them:\n"
            "  - a wide-angle or action camera; re-clicking will not help.\n"
            "    Re-run with --frame --lens: if the paint in that frame\n"
            "    bows, the lens is measurable and correctable from it, and\n"
            "    if it does not bow, the answer comes back honestly empty\n"
            "  - a misplaced or mislabelled landmark; start with the point\n"
            "    listed above and check the outline against the painted lines\n"
            "  - a guessed pitch size; every metre is scaled by it\n"
            "  - markings that are not to the Laws. Unlike the other three\n"
            "    this one is measurable, but not from here: re-open the file\n"
            "    in calibrate/, which fits the markings to the clicks and\n"
            "    offers what it measured. This tool fits what the file says."
        )

    out = args.out or Path("cv/calibrations") / f"{args.points.stem}.calib.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    calib.save(out)
    print(f"\nsaved -> {out}")
    return 0 if good else 2


def _non_standard_marks(pitch):
    """(label, "measured, Laws say x") for every marking that has moved."""
    from cv import pitch as P

    rows = []
    for field_name, laws, label in (
        ("penalty_area_length_m", P.PENALTY_AREA_LENGTH_M, "penalty area depth"),
        ("penalty_area_width_m", P.PENALTY_AREA_WIDTH_M, "penalty area width"),
        ("goal_area_length_m", P.GOAL_AREA_LENGTH_M, "six-yard box depth"),
        ("goal_area_width_m", P.GOAL_AREA_WIDTH_M, "six-yard box width"),
        ("penalty_spot_m", P.PENALTY_SPOT_M, "penalty spot"),
        ("goal_width_m", P.GOAL_WIDTH_M, "goal width"),
        ("centre_circle_radius_m", P.CENTRE_CIRCLE_RADIUS_M, "centre circle"),
    ):
        value = getattr(pitch, field_name)
        if abs(value - laws) > 1e-9:
            rows.append((label, f"{value:.2f}m, the Laws say {laws:.2f}m"))
    return rows


def _estimate_lens(args):
    """Measure the lens from the painted lines, or return None and say why.

    This runs before the homography is fitted, and it has to. `Calibration.fit`
    needs the clicks straightened before it sees them; a homography already
    fitted to bent pixels has absorbed some of the bend, and there is no
    separating the two afterwards.

    Returning None on a frame that cannot answer is the point, not a fallback.
    A line through the image centre stays straight under any coefficient, so a
    frame whose paint all runs near the centre has genuinely nothing to say --
    and fitting a number to it anyway would move every landmark for no reason.
    """
    try:
        import cv2

        from cv.distortion import lens_for_frame
    except ImportError as err:
        print(f"\nlens check unavailable: {err}")
        return None

    frame = cv2.imread(str(args.frame))
    if frame is None:
        # `_check_against_paint` reports this properly and stops the run a few
        # lines later. Saying it twice helps nobody.
        return None

    model, fit = lens_for_frame(frame)
    print(f"\nlens          {fit.summary()}")
    if model is None:
        print(
            "              Not applied. The clicks are fitted as they were, which\n"
            "              is the right answer when the frame cannot tell a lens\n"
            "              from a straight one -- an unsupported coefficient moves\n"
            "              every landmark and improves nothing."
        )
        return None

    print(
        "              Applied. Every number below is measured through it, and\n"
        "              it is saved with the calibration, so anything that loads\n"
        "              this file corrects for the lens without being told to."
    )
    return model


def _check_against_paint(calib, args):
    """Grade the calibration against paint nobody clicked. Returns (calib, code).

    A non-None code means stop and exit with it. The returned calibration is
    the refined one when `--refine` was passed and the refinement was accepted,
    which is why the caller re-measures the click-based errors afterwards: the
    homography that gets saved has to be the homography that got graded.
    """
    try:
        import cv2

        from cv.lines import detect_segments, draw_pitch_lines, fit_to_lines, refine
    except ImportError as err:
        # A machine that only wanted to fit a homography does not need OpenCV
        # installed, and losing the optional half of this report is not a
        # reason to lose the calibration with it.
        print(f"\nline check unavailable: {err}")
        return calib, None

    frame = cv2.imread(str(args.frame))
    if frame is None:
        print(f"error: {args.frame} is not an image OpenCV can read")
        return calib, 1

    height, width = frame.shape[:2]
    if calib.image_size and tuple(calib.image_size) != (width, height):
        # Not a warning. A homography is in the pixels of one particular image;
        # graded against a frame of another size, every number below would be
        # wrong by the ratio between them, and wrong quietly.
        print(
            f"error: the frame is {width}x{height} but these points were clicked "
            f"on {calib.image_size[0]}x{calib.image_size[1]}"
        )
        return calib, 1
    if not calib.image_size:
        print("\nnote: the export carries no frame size, so this frame is taken on trust")

    segments = detect_segments(frame)
    print(f"\nline segments {len(segments)} found in {args.frame.name}")
    if not segments:
        print(
            "              No paint at all. A frame grabbed during a stoppage, a\n"
            "              dark or washed-out frame, or a camera pointed at the\n"
            "              wrong half all look like this. Nothing else here is\n"
            "              affected -- the calibration is saved as it was."
        )
        return calib, None

    print(f"against paint {fit_to_lines(calib, segments).summary()}")

    if args.refine:
        refined, _, after = refine(calib, segments)
        if refined is calib:
            print(
                "refined       no. Every candidate was worse in a way that\n"
                "              mattered, so your clicks were kept untouched."
            )
        else:
            print(f"refined       {after.summary()}")
            print(
                "              The two figures above this block are re-measured\n"
                "              on the refined homography, so they may read worse\n"
                "              than your clicks alone managed. That is the trade:\n"
                "              the paint is evidence and the clicks are a guess."
            )
            calib = refined

    print(
        "\n              Read coverage before the errors. A calibration sitting\n"
        "              off the pitch matches almost none of the paint and then\n"
        "              scores a small error over the little it did match, so a\n"
        "              low coverage makes the metres beside it meaningless.\n"
        "              And a line fit that passes is worth roughly three metres\n"
        "              at the worst landmark, not the 1.5m the click-based check\n"
        "              promises: paint pins a calibration across the lines and\n"
        "              lets it slide freely along them."
    )

    if args.overlay is not None:
        args.overlay.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.overlay), draw_pitch_lines(frame.copy(), calib))
        print(f"\noverlay    -> {args.overlay}")
        print(
            "              Worth more than any number here: an outline that sits\n"
            "              on the paint is right, and one that does not is wrong\n"
            "              in a way you can point at."
        )

    return calib, None


if __name__ == "__main__":
    raise SystemExit(main())
