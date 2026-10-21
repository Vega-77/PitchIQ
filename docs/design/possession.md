# `pitchiq/possession.py`

Moved verbatim from comments in the deep clean (C-2). The code keeps what each
value is; this keeps the reasoning and the measurements behind it.

### Possession radius

How close a player must be to count as having the ball, in multiples of the
median player height in that frame. About a player's own height on the ground,
which is roughly the distance a player can actually reach the ball from.

This was 1.6 — a stride and a half — chosen as a reasonable-sounding number
and never checked. On one minute of tactical footage checked frame by frame by
somebody who watched it, 1.6 credited the dark shirts 61% of possession where
the human counted 34%, and it stayed near 61% no matter what else was changed.
At 1.0 the split lands between 39% and 47% across every other setting tried.
The mechanism is simple enough: at a stride and a half a player who is merely
in the same part of the pitch as the ball can win the nearest-player test, and
the team with more bodies in shot wins more of those.

### Holder margin

How much nearer the closest player must be than the closest opponent before
the frame is credited to a team, in player heights. Zero credits whoever is
nearest by any margin at all, which is what this module used to do and what
made the possession split a headcount: the ball centroid wanders a couple of
pixels a frame, so inside a crowd the nearest player is close to a coin toss
among everyone standing there, and coin tosses resolve in proportion to how
many of each shirt happen to be in shot. Measured on one minute of tactical
footage, possession tracked each team's share of on-screen player-time to
within three points while the human watching it counted almost the reverse.
A margin says: unless one team is plainly closer to the ball than the other,
nobody gets the credit and the frame is contested.
Half a player height, measured on the same minute: it takes the turnover count
from 13 to 5 against a human count of 3, and costs live time rather than
accuracy — the frames it drops are the ones nobody could have called.

### Local scale

How many of the nearest players to measure the scale from. Zero uses the
frame median, which is the wrong ruler wherever the ball actually is: on a
wide shot a figure near the camera is several times the pixel height of one
at the far touchline, so a frame-median radius is far too generous at the top
of the picture and far too mean at the bottom. The players closest to the
ball stand at roughly the ball's own depth, so their height is the local
scale. pitchiq/touches.py has measured its radii this way for a while; possession
never did.

Left off, against expectation. Tried at 3 and 5 on the checked minute and it
made the split worse both times (dark 66-73% against a human 34%), because the
frame median here is dragged *down* by distant figures rather than up by near
ones, so measuring locally widens the radius instead of tightening it. Kept as
a switch because that balance is a property of this camera position, not a law.

### No-ball time

How much of the window had no ball in it at all.

This is a *diagnostic*, not a fourth bucket: the spells above already tile
the whole window, and a frame with no ball resolves to no holder and so is
already inside `contested_s`. It is reported separately because those two
are very different things to a coach — "both teams were fighting for it"
and "we could not see it" read identically in a possession split, and only
one of them is football.

It used to be computed as span minus the other two, which the tiling makes
identically zero. That is the sort of number that looks fine forever.

Taken as a share of the window rather than a count of frames times a frame
duration. The spells above measure the intervals *between* timestamps, of
which there is one fewer than there are frames, and a count-based figure
here would sit a frame above the window it is supposed to fit inside.
