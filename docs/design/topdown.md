# `pitchiq/topdown.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Top-down map: everyone on the pitch, every frame, smooth, honest about who is unseen.

`pitchiq/linking.py` says who each box is. This module says where each person
stands, in metres, for every frame of the window, including the frames the
camera is looking somewhere else. Three steps:

* **Feet.** A box's bottom centre through that frame's homography. Its noise
  is a few pixels pushed through the homography, so a far-side foot, where a
  pixel is a yard, counts for less than a near one. A box much shorter or taller
  than a 1.75 m person standing at its feet (the camera knows how tall that is
  in pixels) is probably someone half hidden behind a nearer player, or two
  people in one box, and counts for far less.

* **Slots.** Per team, ten outfield slots and a keeper slot, and a slot for the
  referee. The linker's identities are chained into slots by a min-cost flow:
  an identity that ends and one that starts later, plausibly the same person
  (near enough for the time between, not walking through the camera's view
  unseen), share a slot. The unit is a visit, a run of sightings, not a whole
  identity: an identity seen at 0-10 s and again at 30-40 s must not block a
  slot through 10-30 s, when someone else may well be the one in it. A slot is one place in the line-up: seen at times,
  estimated in between. Identities that do not fit in eleven slots (a bib read
  as the wrong kit, a fragment) are drawn while seen and nothing more.

* **Smooth.** Each slot's feet go through a robust Rauch-Tung-Striebel
  smoother. Velocity reverts over about a second (Ornstein-Uhlenbeck) to
  most of the mean velocity of the teammates in the picture, so a player who
  leaves it moves with their team rather than running on forever or freezing
  (hidden 10 s on the dev clips: 3 m out when they come back, 6-8 m when they
  never do, against 9-19 m for standing still), and a blink of the detector is
  bridged in a curve rather than a jump. The smoother's spread says how much to fade an unseen dot, and
  it fades out altogether where the guess is contradicted: the picture has
  shown that spot for over a second with nobody there, or a teammate is seen
  standing on it. (Pushing the guess out of the picture instead moves it at
  the speed of a pan, which is no one's running speed.)

Positions are in metres on the pitch (x along, y across, 0..length, 0..width);
callers convert to whatever their drawing uses.
