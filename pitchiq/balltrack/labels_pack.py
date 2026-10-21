"""Pack a lab's label download (one JSON file per answer) into one JSONL file.

    python -m pitchiq.balltrack.labels_pack <dir> [<dir> ...]     -> <dir>.jsonl, one ["<name>.json", doc] per line

The labs' answers come down from their artifact databases as one file per
document, and in that form 2,100 small files made up most of the repo. Each
set now lives in one file beside where its directory was: `labdb5/crops.jsonl`,
`alltests/findball/labels.jsonl`, and so on. A line keeps the original file
name and the document exactly as it was, in file-name order (the order every
reader used to glob them in), so nothing is lost and no reader's input moves.

Pack a fresh download with this, then delete the directory. `truthdb/` stays
as it is: it is the test set, and nothing here touches it.
"""

import glob
import json
import os
import sys


def pack(dirn):
    files = sorted(glob.glob(os.path.join(dirn, '*.json')))
    if not files:
        sys.exit('%s: no .json files' % dirn)
    if 'truthdb' in os.path.abspath(dirn).replace(os.sep, '/').split('/'):
        sys.exit('truthdb is the test set; it is not packed')
    out = os.path.normpath(dirn) + '.jsonl'
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        for p in files:
            with open(p, encoding='utf-8') as g:
                doc = json.load(g)
            f.write(json.dumps([os.path.basename(p), doc]) + '\n')
    print('%s: %d docs -> %s' % (dirn, len(files), out))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for d in sys.argv[1:]:
        pack(d)
