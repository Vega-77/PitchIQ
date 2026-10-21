# `pitchiq/zones.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Where on the pitch something happened.

Every event statistic beyond a raw count is really a question about geography.
A pass is progressive if it gained ground *towards a particular goal*. A cross
comes from a wide area *of the attacking third*. A clearance leaves the
defensive third. None of those mean anything until you can say which end of the
pitch a team is playing towards.

So `attacking_end` (or `end`, or `defending_end`) is a required argument
throughout this module, never defaulted. That is deliberate and slightly
annoying. The failure mode it prevents is the one `Pitch.to_statsbomb` already
warns about: getting the end wrong does not raise, it silently reports a
defensive clearance as a final-third entry and a shot from your own box as a
tap-in. A wrong number that looks right is worse than a crash.

Everything here is in metres and needs a calibration upstream. The functions
themselves are pure geometry over `Pitch`, so they are fully testable today —
which matters, because the footage that would let them run for real does not
exist yet.

Deliberately absent: offside. It needs the complete and accurate positions of
every defender at the exact moment of release. We have neither complete
positions (players leave frame) nor an accurate release moment (the ball is
detected in about 60% of frames). It stays human-tagged; see the ROADMAP archive, Phase 10.
