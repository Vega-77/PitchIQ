"""TAPNext++ bridges over the tracker's gaps (tn_engine) and the review videos."""

import os

# TAPNext's crop window, px (env TNS); the network also runs at this resolution.  The checkpoint was tuned at
# 512.  384 from 2026-10-07: it halved the time for the same wrong picks on the 22 ball labs (2 fewer right of
# 158).  256 from 2026-11-24 (session step 3.16): on eight games' Ball Clicker answers it scored the same as 384
# (535 right / 12 wrong), its tracks sit ~1.3 px from 384's at the median, and it bridges a frame in about half
# the time (43 against 89 ms), ~70 min less a game.  wbr's events moved a little more than random 1.5 px noise
# does (906 of 1035 kept against 939).  The owner chose 256.
WINDOW = int(os.environ.get('TNS', 256))
