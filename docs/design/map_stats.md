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

## Territory and heatmaps

`territory.py` splits possession by third from the ball, and the ball is not
trustworthy yet (possession flipped answer in half its frames when the ball
moved 20 px). So territory here is read off the players:

* play: the centre of every outfielder on the map, both teams and greys. It
  needs no team split, so it is kept in the windows `split_checks` drops. As a
  share of time in each third, it says where the game was.
* height: a team's outfield centre, metres from its own goal.
* heatmap: a team's outfielder time on the 6 x 5 zones of `zones.zone_grid`,
  turned so it attacks to the right (a half turn, so the left wing stays the
  left wing). Its thirds are the column pairs. Each zone also carries the seen
  part of its time: a zone the camera rarely showed is mostly a guess.

All 9 dev halves (`PitchIQ-data/stats/terr_dev.md`), median over halves and
teams; heat is the share of time put in a different zone:

| | unit | 5-min sd (real) | small errors, worst | expected (missing + extras) | seen only | stray every instant | edge ring every instant |
|---|---|---|---|---|---|---|---|
| play, team 0 attacking third | pp | 12.6 | 0.4 | 0.3 | +1.4 | -2.5 | -2.5 |
| play, team 0 own third | pp | 11.8 | 0.4 | 0.3 | +1.6 | -2.0 | -2.0 |
| height | m | 10.2 | 0.3 | 0.0 | +0.7 | +0.2 | +0.2 |
| time in attacking third | pp | 11.8 | 0.4 | 0.1 | -0.2 | +1.0 | +1.0 |
| time in own third | pp | 13.4 | 0.5 | 0.1 | +1.5 | +0.2 | +0.3 |
| heatmap | % | 20.4 | 1.9 | 0.7 | 2.8 | 2.5 | 7.2 |

Reading: territory is sturdier than width and depth, because it is a mean or a
count, not a max-minus-min. No realistic error moves a share by more than half
a point against real swings of 12-13 points, or a height by more than 0.3 m
against 10 m. Even a stray ring every instant moves the play shares 2-2.5
points. The heatmap's 5-minute blocks differ from the half by 20% of their
time; wrong input moves 1-2%, and edge rings 7% (they pile into the touchline
zones).

The halves read like their shapes: the team with the higher back line also
sits higher and has play in its attacking third more (wb1 team 0: height 60 m
vs 45, play 36% in its attacking third vs 13% in its own; pi2 team 0 the
other way, 53% of its time in its own third).

Seen part of each zone, median: 79-92% in the own half, falling to 66-75% in
the corners of the attacking third, where the camera looks least. A pinned
team's attacking zones are the most guessed (pi2 team 0: 44-65% seen there),
so `heat_sheet` prints the seen part under every zone.

## In the report (schema 16)

`map_stats.report_block` shapes the map's team figures for the report's teams
block, and `python -m pitchiq map_report <map.npz> --ours lighter|darker`
writes them as a report JSON (alone, or `--into` a pipeline report), which
`publish_report` sends to Firestore like any other. What goes in, from the
measurements above:

* `shape`: `line_m` and `compactness_m`. `width_m` and `depth_m` are null on
  purpose: a stray ring sets a max-minus-min, and they lean 3-5 m on guesses.
* `field_position`: `height_m`, `third_share`, `play_third_share`, a 21 x 14
  heatmap (5 m cells, turned to attack right) with `seen` per cell,
  `seen_share` and `kept_share`.
* Every figure in a window whose team split failed is left out, and a team
  with no kept window gets null, never 0.

Which map team is the coach's side cannot be read off the map, so `--ours` has
no default; the shirt shade (`lighter_team`, median kit L after the window
swaps) names the two sides the way the heat sheet does.

On the coach's match page they are the "Where the teams stood" rows (beside
Shape) and the "Where each team stood" block: one pitch a side with the thirds
dashed and the guessed ground (seen under 70%) hatched.

## Not done yet

- Live play only: shape at a corner or a goal kick counts like open play.
- In and out of possession: needs the ball (not trustworthy yet).
- The real stray rate is a lower bound from the counts, not from labels.
- The linker's team split in bright, low-sun and dark-kit windows (fixing it
  would win back the 9 dropped windows).
- Held out (nb2, pi1, wh1, co1, wb2) waits for one final score.
