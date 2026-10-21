# pitchiq/experiments/clicker/

The Ball Clicker: frames from every half where a person clicks the ball (or
answers out, dead, can't tell), to score the ball rings the videos draw.

- `build.py` draws the items: 75 frames per half over 12 halves, split into 5
  batches that each cover every half end to end.
- `stage.py <site dir> b1 [b2 ...]` stages `index.html`, `batches.json` and the
  live batches' media for publishing.
- `clickscore.py` scores the pipeline's rings against the downloaded answers.

Media and answers live in `PIQ_DATA` (`PitchIQ-data` beside the repo), not here.
