# `pitchiq/map_stats.py`

Team statistics read off the top-down map. Started 2026-11-06, when the work
moved from the map and ball to the numbers built on them. The user's pick:
coach first (team numbers), players later; shape first, then territory and
team heatmaps.

## Why a new module

`metrics.team_shape` already defines width, depth, compactness and line
height, but it reads one `PositionSeries` per track from the older tracking
path, and loops instant by instant in Python. The map is a table: every dot of
every frame, seen or guessed, in a team or grey. `map_stats.shape` keeps the
same definitions (`metrics.line_players`, `MIN_LINE_OUTFIELDERS`, the
deepest-outfielders line of `zones.defensive_line_m`) on that table.

## Whole halves

Until now the map was only built for the Map Lab's 3 x 3-min windows a half.
Everything it needs covers whole halves for all 14 halves (detections,
features, `<tag>_smooth2` registrations), so `experiments/wholemap.py` links
and maps each half in back-to-back 3-min windows, 8 at a time: 10-27 min a
half (wb1's map step is the slow one). Output:
`PitchIQ-data/stats/map/<tag>.npz`, about 1.7-2 M dot rows. All 9 dev halves
were mapped on 2026-11-06 (eb1 eb2 ww1 ww2 nb1 wb1 wh2 pi2 co2).

Each window is linked alone, so `team_a` / `team_b` trade names between
windows (2 to 12 of 14-17 a half). `wholemap.align` lines them up by shirt
colour (median Lab of each team's player boxes against the running consensus).

## Team split check

Checked by keepers (`PitchIQ-data/stats/check_align.py`): in a window where
both keepers were seen 5 s or more, team 0's keeper should stand at the half's
team 0 end. Over the 9 dev halves 91 windows agree and 8 disagree (ww1 2,
nb1 2, pi2 3, co2 1); the rest had a keeper out of view.

The 8 are not colour-alignment slips. Pictures (`stats/sheets/`) show the
linker's split itself went wrong in them: on ww1 77400 and 93600 one "team"
took 9.5 seen players a frame and the other 3-4, with 2-3 greys; good
windows run about 8 and 8 with under 1 grey. Brighter exposure (ww1, nb1),
low sun (pi2) and two dark kits (co2) are where it happens. In nb1 the
right-end keeper was also given to the attacking team (the linker picks a
keeper's team by which team stands deeper).

`map_stats.split_checks` judges each window from the map alone: seen
outfielders per frame of the smaller team over the larger (`SPLIT_RATIO`
0.65) and seen greys per frame (`SPLIT_GREY` 2.5). The cut was set by the
keepers: all 8 disagreeing windows have ratio < 0.65 or greys >= 2.7, and
windows with 1-2.3 greys and agreeing keepers are kept. One window with
agreeing keepers is dropped by the ratio (pi2 199800, 0.61). In a dropped
window `shape` gives NaN for both teams (wrong numbers are worse than
missing ones); the map still draws every ring. 126 of 135 windows kept.

## Which end a team defends

By keepers first. On the eb1 probe the keepers stood at x 4 and 110 while the
seen outfielders' means were only 5-10 m apart (both teams crowd the end where
the ball is, and the camera follows it). Seen-outfielder means are the
fallback.

## What counts

Outfielders only (`role == player`), in a team; keepers, referees and grey
rings are out of every team number. Greys stay on the map: the user's rule is
that they are never hidden, and leaving them out of a sum does not hide them.
Guessed dots (unseen players moving with their team) are counted by default:
the camera shows part of the pitch, so seen-only shape is the shape of what was
in view. About 80% of counted outfielders are seen (73-87% by half and team).

## Wrong input (`experiments/stats_noise.py`)

Each kind is applied at every sampled instant (2 a second); "expected" scales
missing players by the measured mean missing count and extras by the share of
instants with more than ten outfielders (both lower bounds). The dropped
windows are judged once, on the unperturbed map, so only the error moves a
number.

All 9 dev halves, median over halves and teams, metres (trim 0;
`PitchIQ-data/stats/shape_dev.md`):

| | 5-min sd (real) | small errors, worst | expected (missing + extras) | seen only | one stray every instant |
|---|---|---|---|---|---|
| line | 9.6 | 0.3 | 0.5 | 0.0 | -2.9 |
| compact | 1.1 | 0.3 | 0.2 | -1.1 | +2.8 |
| width | 2.8 | 0.8 | 0.6 | -3.3 | +4.1 |
| depth | 2.7 | 0.7 | 0.7 | -4.8 | +18.7 |

Small errors: 1 m jitter, 1 m bias, one team flip, 2% calibration scale.
Measured rates: a team shows more than ten outfielders in 7% of instants and
is 0.62 short of ten on average.

Half means by team (line / compact / width / depth): back lines 27-52 m from
their own goal, compactness 12-16 m, width 29-44 m, depth 28-36 m. The two
teams of a half differ by 9.5 m in back line (median), 15 times the error.

Reading: the back line is the solid number. It moves about 10 m from one
5-minute block to the next for real, and no realistic error moves it more
than half a metre. Compactness is next. Width and depth are max-minus-min, so
one wrong ring sets them: a stray every instant adds 4 m to width and 19 m to
depth. At the measured rates the misses and extras roughly cancel in the
mean, but they lean 3-5 m on where the map guesses unseen players are, and a
guessed dot is more than 1 m off the pitch about 4% of the time (seen dots
about 1%). Trimming the outermost dot each side (trim 1) cuts the stray damage
to depth (+3..+4 m) but not width off a touchline (+6..+7), and doubles the
missing-player damage.

## Not done yet

- Live play only: shape at a corner or a goal kick counts like open play.
- In and out of possession: needs the ball (not trustworthy yet).
- The real stray rate is a lower bound from the counts, not from labels.
- The linker's team split in bright, low-sun and dark-kit windows (fixing it
  would win back the 9 dropped windows).
- Held out (nb2, pi1, wh1, co1, wb2) waits for one final score.
