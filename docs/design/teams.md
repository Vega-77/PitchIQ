# `pitchiq/teams.py`

Moved verbatim from comments in the deep clean (C-2). The code keeps what each
value is; this keeps the reasoning and the measurements behind it.

### Axis margin

How much better than chroma another axis has to score before it takes the
split. Not a tie-break: the two axes are not equally trustworthy. Chroma is
what survives a cloud crossing the sun, and grass-relative lightness only
approximately survives it -- the grass reference assumes shadow dims shirt
and turf by the same factor, which is close to true and not true. So chroma
holds the split unless the evidence against it is plain.

The two measured cases sit either side of this. Red against yellow, where
chroma works perfectly well, scored 1.25x on lightness -- a real margin, and
not a reason to give up the safer axis. The dark kit against the white one,
where chroma is blind, scored 2.3x. Neither is near 1.5.

### Axis coverage

What fraction of tracks must actually carry an axis before it may be used.
Not every track has every reading: a detection at the very edge of frame has
no turf beside it to read, so it has no grass-relative lightness. Those
tracks end up UNKNOWN on that axis, which is what UNKNOWN is for.

This was one track for all, and it silently cost the axis its first run on
real footage: across 477 fragmented tracks at least one had no reading, so
"all finite" was never true and the axis was never offered. What the rule is
actually protecting is the fit -- centres drawn from a fifth of the pitch are
not the pitch -- and that wants a fraction, not unanimity.

### Cluster share

The smallest share of tracks the lesser cluster may hold and still be called
a team. Separability alone does not ask this, and on real footage that let it
be gamed: chroma scored 2.19 by setting 81 tracks against 608, a tight clump
at a 159 b 144 -- the pitch is multi-use turf with orange and yellow lines,
and that clump is line paint and skin caught in torso boxes, not eleven
players. Grass-relative lightness scored 1.87 splitting 281 against 408 with
its centres 100 apart, which is a dark kit and a white one. The lower number
was the right answer, so the number was not asking enough.

Two teams are eleven and eleven. Fragmentation, substitutes and a keeper in a
third colour move that around, and one team being nearer the camera moves it
further, so the bar is set low enough to survive all of it and high enough
that no clump of outliers clears it.

### Split by frames

With track lengths given, each candidate axis is also split by frames rather
than by tracks (`_weighted_two_means`), and a winner that still leaves the
smaller team under BALANCED_SHARE gives way to the other axis when that one
divides the frames more evenly. The case is a hazy, half-sunlit minute at
North Brunswick: chroma put sunlit white on one side and blue with shaded
white on the other (8% of the frames against 92%), while lightness against
the grass had the real gap. On the spot-lab team answers over seven dev
halves this took 142 -> 152 of 175 right and 14 -> 10 wrong; no half got worse,
and fm01/fr00 scored the same.

### Tint refit

Once grass-relative lightness has split the pitch, the split is refitted with
the shirt's tint alongside its lightness: a two-class discriminant with one
shared covariance, started from the lightness split. The case is a team in
training bibs. A navy bib over a white shirt is neither kit's lightness -- on
the dev clips bib tracks sat at -62..-33 against a black kit's -97..-39 -- so
no line on lightness alone puts them with their team. Their tint does: the
black kit read b* +7 under a grass cast, white shirts and navy bibs about -2.
Which channels separate is a fact about one fixture's kits and light, so the
weights are fitted per fixture rather than chosen here; where tint says
nothing, the fit leaves lightness carrying the split.

Huber weights keep the covariance from being set by what is not a player:
a coach in yellow and a bench in orange bibs sit far off both kits on b*, and
a plain fit tilted the line toward them. Medians, not means, for the centres.
