# balltrack/crop_labels/

`crops.jsonl`: the 1520 Crop Lab labels (ball, cleat, head, otherball, other,
unsure) on detector candidates, packed one document a line by
`labels_pack.py`. `retrain.sh` trains the crop classifier (`cls_B.pt`) from
them; it also needs the crop caches, which are not in git (see the balltrack
README).
