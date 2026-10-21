# Glossary

Terms the code uses without defining them anywhere else. Where a module owns
the term, it is named.

## Pitch, camera and coordinates

**Pitch metres.** The real pitch, origin at the bottom-left corner of the
broadcast view, x along the touchline, y along the goal line. Distance, speed
and every `_m` key are in these (`pitchiq/pitch.py`).

**StatsBomb 120 x 80.** The pitch scaled to 120 x 80 units whatever its real
size: the frame the xG model was trained in (`xg_training/`) and the one the
ball tracker's outputs use. `_sb` in a name.

**Drawn pitch (105 x 68).** The standard drawing every page plots into. Shot
maps and published event positions are converted to it (`report_export.drawn`)
so a measured 112.7 m pitch and the default plot the same way.

**Player heights (`_ph`).** A distance measured in the height of the person
nearby in the image, so it means the same near and far from the camera without
a calibration. Used by participants and possession, which must work uncalibrated.

**Camera heights (CH).** The ball tracker's 3D unit: the camera sits at height
1 above the ground plane. A flight's height `z` is in CH until it is converted.

**Source px.** Pixels of the original 1920 x 1080 video frame, as opposed to a
resized or stabilised one.

**Fixed camera / panning camera.** The two pipelines. A fixed camera has one
homography for the whole match (`pitchiq/calibration.py`). A panning camera
(the team's Hudl footage) needs one per frame (`pitchiq/panning_camera.py`).

**Venue map.** Keyframes of one match's video, each placed by homography in
the image plane of one reference keyframe, so the whole ground the camera ever
saw is one picture (`balltrack/pitch_registration/venue_map.py`). Each camera
needs its own.

**Reference view.** That reference keyframe's image plane. A panning camera's
boxes and ball are moved into it, and from there one fixed calibration maps them
to metres, so the rest of the pipeline sees a still camera.

**Registration.** Placing a frame on the venue map (`register_frames.py`), every
half second. Between registrations the **pan** (`<tag>pan.npz`, frame-to-frame
affines chained as `cum`) carries it.

**Autocal.** Calibrating a panning camera from the match itself, using the
painted lines and player sizes, with no clicked pins (`pitchiq/autocal.py`).

**Pins.** Landmarks a person clicked on the calibrate page. Pins stay human:
nothing overwrites them with fitted values.

**Framing.** Whether the camera made the players big enough to detect.
`detection_diagnosis.assess_framing` splits "the camera's fault" (players too
small in the video) from "ours" (too small at the size the detector runs at).

## Match time

**Tag log.** What the touchline tablet records (`live-tagging/`): kick-off,
restarts, substitutions, goals, with the tablet's clock. Read through
`shared/db.js::listLog`. It is the only source for when the ball was out.

**In play / dead ball.** Whether the ball is live. Dead-ball spans open at a
tagged stoppage and close at the restart (`pitchiq/phases.py`). Without a tag
log every stoppage counts as play.

**Video clock.** The mapping between the tablet's clock and the video's
(`phases.VideoClock`): an offset, plus where the second half starts in the
file, because the tablet's clock stops at half time and the video does not.

**Blindness.** The stretches where the ball was not seen, sorted into dead,
accounted (a tag nearby explains it), unexplained (a real hole) or unchecked
(no tag log) (`pitchiq/ball_visibility.py`). The JSON keys still say `blind`.

## People

**Track.** One detection followed from frame to frame (`pitchiq/tracking.py`,
`pitchtrack.py`). A track ends when the tracker loses the person.

**Tracklet / fragment.** A track that is only part of a person's match. At
current fragmentation a player spans several.

**Identity.** Tracklets joined offline under the rules a match obeys
(`pitchiq/linking.py`).

**Cluster.** Tracks grouped by appearance as probably one person
(`pitchiq/identity.py`, `PlayerCluster`). A coach confirms a cluster and maps it
to a roster name. Until then per-player figures are figures per fragment.

**Participant.** The verdict on whether a track is a `player`, `offfield`,
`official` or `unsure` (`pitchiq/participants.py`), with the evidence.

**Touch.** A moment someone had the ball at their feet (`pitchiq/touches.py`).
Possession, passes and every event are built from touches.

## Ball tracker

**Tag (of a window).** A short name for one stretch of one video, such as
`fr00` or `nb1`. Every tracker file is named `<tag>...`.

**abs frame.** A frame number in the whole video, as opposed to an index
inside a window.

**Candidate.** A blob the detector proposes as maybe the ball, with a score
(`detect_candidates.py`, `<tag>.npz`).

**cls_B.** The shipped crop classifier (`cls_B.pt`) and the picks the tracker
makes with it (`evaluation/p2d/cls_B/`). `cls_A` is the other variant `retrain.sh`
builds.

**Hybrid 90.** The accepted tracker: cls_B's picks, with gaps of up to 90
frames on the ground bridged by TAPNext++ when its forward and backward runs
agree, then the 3D flight chain (`balltrack/hybrid.sh`). `hyb32` caps the gap
at 32 frames.

**TAPNext++.** A point tracker from a public repo, used only to bridge gaps
(`balltrack/tapnext_bridge/`). Its checkpoint is not in git.

**Flight.** A stretch where the ball is in the air, fitted with a drag model.
**Chain**: touching flights fitted together as one path of nodes on or off the
ground (`flight_chain.py`). **Strict flights** drop any flight a wrong guess
could explain (`strict_flights.py`): a wrong flight is worse than a missed one.

**Ball gate.** Blanking whole stretches where the tracker's confidence stays
low, instead of judging rings one at a time (`ballgate.py`).

**Dev / held-out.** Halves used for tuning, and halves kept back for scoring
only. Nothing is tuned on held-out halves, and `ground_truth/` is never trained on.

**Labs.** Hand-labelling pages published as artifacts: Spot Check Lab (yes/no
questions on drawn output), Ball Clicker (click the ball), Air Lab and Flight
Lab (which frames are in the air, where flights start and land), Fix Lab, Crop
Labeler. The label sets kept in git live in `balltrack/pitch_registration/labeling/`,
`balltrack/crop_labels/` and `balltrack/ground_truth/`.

## Tests

**Seam test.** A test that checks two sides of a boundary agree on names: the
keys Python writes and the keys a page reads, the ids a script looks up and the
ids the HTML declares, the CSS classes used and defined. Neither side can see
the other, so a rename on one side breaks nothing until a page silently shows
a dash. `tests/README.md` lists them.

**Meta-test (hygiene test).** A test about the code itself rather than its
behaviour: call graph, duplicate bodies, unread names. Their allowlists hold
entries with a reason; `tests/README.md` says what each protects.
