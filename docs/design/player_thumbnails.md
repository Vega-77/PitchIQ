# `pitchiq/player_thumbnails.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

A recognisable picture of each tracked figure, cut while the pixels exist.

The mapping step is the one part of this pipeline that cannot be automated and
has never been made easy. A coach opens the review tool and is shown forty
tracked figures described as *"team a · 3 fragments · 12:04–19:31 · 2,410
frames"* beside a swatch of kit colour, and has to decide which teenager each
one is. The sub log narrows the list (Phase 7) and the frame count says which
figures are worth the effort, but neither answers the actual question, which is
visual: *that is the tall one who plays left back.*

So this cuts one small picture of each track out of the footage. It has to
happen inside the decode loop for the same reason `_sample_colours` does — what
survives a batch is a few numbers per detection, never the image — which is why
this is a scoring function and an encoder rather than a pass of its own.

Three rules, and each of them is about not showing a coach something worse than
nothing:

  * **Never upscale.** A player forty pixels tall gets a forty-pixel thumbnail.
    Blowing it up to a tidy uniform size would invent detail the sensor never
    recorded and make an unusable crop look like a usable one. A row where the
    picture is visibly tiny is telling the truth about that figure.
  * **Nothing clipped by the frame edge.** Half a player is not recognisable and
    is actively misleading — the missing half reads as a smaller person.
  * **Nothing wider than it is tall.** That box is two players the detector
    merged, or somebody on the ground. Either way it is not a portrait of one
    person, and a coach who matches a name to it has mislabelled a whole track.

A rejected candidate is not a rejected track: the next sighting is a few frames
away, and over a half every track that was ever cleanly seen gets its picture.
A track that never was is a track the tracker was struggling with anyway, and it
keeps its colour swatch.
