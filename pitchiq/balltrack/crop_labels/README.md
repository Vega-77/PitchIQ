# balltrack/crop_labels/

`crops.jsonl`: the 1520 Crop Lab labels (ball, cleat, head, otherball, other,
unsure) on detector candidates, packed one document a line by
`labels_pack.py`. The ball step (`ball17.py`) trains its classifier on them for
every game; it also needs the crop caches, which are not in git (see the balltrack
README).
