"""Stage the Ball Clicker site for publishing: the page, batches.json, and the live batches' media.

    python stage.py <site dir> b1 [b2 ...]     -> <site>/index.html, batches.json, batches/<b>/...

Batches not named are removed from the site dir (the artifact keeps them until published as null).
Published at https://claude.ai/artifact/86WZCjEKrvXA47AJLgEsmz (2026-10-16, batch b1). A batch is ~120 MB,
so it goes up in parts of <= 64 MB / 255 files. Answers: db batches/<b>/answers/cNNN, {a: {<item id>:
{v: ball|out|dead|cant, x, y (full-res px of frame f, v=ball only), ms, at}}}, 100 items per doc by
position in that batch's items.json.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = 'C:/Users/alexv/Desktop/Repos/PitchIQ-data/clicker'

site, live = sys.argv[1], sys.argv[2:]
os.makedirs(os.path.join(site, 'batches'), exist_ok=True)
shutil.copy(os.path.join(HERE, 'index.html'), os.path.join(site, 'index.html'))
for b in os.listdir(os.path.join(site, 'batches')):
    if b not in live:
        shutil.rmtree(os.path.join(site, 'batches', b))
rows = []
for b in live:
    src, dst = os.path.join(ROOT, b), os.path.join(site, 'batches', b)
    items = json.load(open(os.path.join(src, 'items.json')))['items']
    os.makedirs(dst, exist_ok=True)
    for f in ['items.json'] + [it[k] for it in items for k in ('still', 'loop')]:
        if not os.path.exists(os.path.join(dst, f)):
            shutil.copy(os.path.join(src, f), os.path.join(dst, f))
    rows.append(dict(name=b, title='Batch %s · %d frames' % (b[1:], len(items))))
json.dump(dict(batches=rows), open(os.path.join(site, 'batches.json'), 'w'), indent=1)
sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(site) for f in fs)
print(site, live, '%.1f MB' % (sz / 1e6))
