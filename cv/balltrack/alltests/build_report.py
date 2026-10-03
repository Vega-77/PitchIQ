"""results/2d.json + results/3d.json -> report.html (every version x every test)."""
import html
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
R2 = json.load(open(os.path.join(HERE, 'results', '2d.json')))
R3 = json.load(open(os.path.join(HERE, 'results', '3d.json')))

# (key, label, group, note)
VERS = [
    ('hyb90', 'Hybrid 90', 'new', 'Ours + TAPNext++ bridges on gaps up to 90 frames (3 s)'),
    ('hyb32', 'Hybrid 32', 'new', 'Ours + TAPNext++ bridges on gaps up to 32 frames'),
    ('hyb90s', 'Hybrid 90 strict', 'new', 'As Hybrid 90, but a rejected gap is left empty'),
    ('cls_B', 'Ours (shipped)', 'ours', 'Classifier B + Viterbi + gap fill: the current tracker'),
    ('cls_B_nofill', 'Ours, no fill', 'ours', 'Shipped tracker without the gap fill'),
    ('cls_Br3', 'Classifier B r3', 'cls', 'Earlier training round of B'),
    ('cls_Br4', 'Classifier B r4', 'cls', 'Earlier training round of B'),
    ('cls_Bprev', 'Classifier B prev', 'cls', 'Previous B'),
    ('cls_A', 'Classifier A', 'cls', ''),
    ('cls_Ar3', 'Classifier A r3', 'cls', ''),
    ('cls_Ar4', 'Classifier A r4', 'cls', ''),
    ('cls_Aprev', 'Classifier A prev', 'cls', ''),
    ('cls_h160', 'Classifier h160', 'cls', 'Earlier generation'),
    ('cls_orig', 'Classifier original', 'cls', 'First classifier'),
    ('blob_v7', 'White blobs + tracker', 'other', ''),
    ('blob_raw', 'White blobs raw', 'other', 'Every blob, no tracker'),
    ('yolo_trk', 'YOLOv8n + tracker', 'other', 'Stock COCO sports-ball'),
    ('yolo_raw', 'YOLOv8n raw', 'other', 'Stock COCO sports-ball'),
    ('tn_alone', 'TAPNext++ alone', 'google', 'Google, seeded once per window; ring where it says visible'),
    ('tn_alone_raw', 'TAPNext++ alone, raw', 'google', 'Ignores its own visibility call (2D only)'),
]
GROUP = {'new': 'Hybrid (new)', 'ours': 'Our tracker', 'cls': 'Earlier classifiers', 'other': 'Other detectors', 'google': 'Google TAPNext++'}


def num(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def f_pct(v):
    return '%.0f%%' % (100 * v)


def f_2(v):
    return '%.2f' % v


def f_1(v):
    return '%.1f' % v


def f_i(v):
    return '%d' % v


def table(cols, rows, caption):
    """cols: (label, getter, fmt, better 'hi'/'lo'/None, title). rows: (key, label, group, note, ctx)."""
    vals = [[g(ctx) for (_, g, _, _, _) in cols] for (_, _, _, _, ctx) in rows]
    best = []
    for j, c in enumerate(cols):
        xs = [v[j] for v in vals if num(v[j])]
        best.append(None if not xs or not c[3] else (max(xs) if c[3] == 'hi' else min(xs)))
    o = ['<div class="tw"><table><caption>%s</caption><thead><tr><th class="v">Version</th>' % caption]
    for (lab, _, _, _, t) in cols:
        o.append('<th title="%s">%s</th>' % (html.escape(t), lab))
    o.append('</tr></thead><tbody>')
    last = None
    for (k, lab, grp, note, ctx), v in zip(rows, vals):
        if grp != last:
            o.append('<tr class="grp"><td colspan="%d">%s</td></tr>' % (len(cols) + 1, GROUP.get(grp, grp)))
            last = grp
        cls = ' class="hl"' if k in ('hyb90',) else (' class="ship"' if k == 'cls_B' else '')
        o.append('<tr%s><td class="v"><b>%s</b>%s</td>' % (cls, html.escape(lab), '<span>%s</span>' % html.escape(note) if note else ''))
        for j, c in enumerate(cols):
            x = v[j]
            if not num(x):
                o.append('<td class="na">–</td>')
            else:
                b = best[j] is not None and abs(x - best[j]) < 1e-9
                o.append('<td%s>%s</td>' % (' class="best"' if b else '', c[2](x)))
        o.append('</tr>')
    o.append('</tbody></table></div>')
    return ''.join(o)


def g(*path):
    def get(d):
        for p in path:
            if not isinstance(d, dict) or p not in d:
                return None
            d = d[p]
        return d
    return get


def flpos(which):
    def get(r):
        t = r.get('fl_txt') or []
        i = 0 if which == 'dev' else 1
        if len(t) <= i:
            return None
        m = re.search(r'pos med\s+([\d.]+).*on\s+(\d+)/', t[i])
        return float(m.group(1)) if m else None
    return get


def fln(which):
    def get(r):
        t = r.get('fl_txt') or []
        i = 0 if which == 'dev' else 1
        m = re.search(r'on\s+(\d+)/', t[i]) if len(t) > i else None
        return int(m.group(1)) if m else None
    return get


# ---------- 2D ----------
rows2 = [(k, l, gr, n, R2[k]) for (k, l, gr, n) in VERS if k in R2]
C2a = [
    ('Truth acc', g('truth', 'all', 'acc'), f_pct, 'hi', 'TruthDB: 213 hand marks over 18 windows (158 ball, 55 off-screen)'),
    ('Right', g('truth', 'all', 'right'), f_i, 'hi', 'ball marks with a ring within 60 px'),
    ('Wrong', g('truth', 'all', 'wrong'), f_i, 'lo', 'ball marks with a ring somewhere else (worst error)'),
    ('Empty', g('truth', 'all', 'none'), f_i, 'lo', 'ball marks with no ring'),
    ('H1', g('truth', 'H1', 'acc'), f_pct, 'hi', 'first-half windows'),
    ('H2', g('truth', 'H2', 'acc'), f_pct, 'hi', 'second-half windows'),
    ('Find right', g('find', 'right'), f_i, 'hi', 'Find the Ball test pool, 47 ball marks'),
    ('Find wrong', g('find', 'wrong'), f_i, 'lo', ''),
]
C2b = [
    ('Pass pts /16', g('passes', 'points'), f_i, 'hi', '8 hand-marked passes, both ends; physical ceiling 14'),
    ('Carried /8', g('passes', 'carried'), f_i, 'hi', 'both ends of a pass hit'),
    ('Fix pts /8', g('fix', 'points'), f_i, 'hi', 'Fix Lab points the shipped tracker missed'),
    ('Fix cover', g('fix', 'cover'), f_2, 'hi', 'share of Fix Lab missed spans with a ring'),
    ('Fix repeat', g('fix', 'repeat'), f_2, 'lo', 'share of Fix Lab WRONG spans where the shipped wrong ring is repeated (lower = better)'),
    ('Jitter px', g('jitter'), f_2, None, 'median 2nd difference of the ring path'),
    ('Coverage', g('cover'), f_2, None, 'share of all frames with a ring'),
]

# ---------- 3D ----------
VAR = [('dd', 'default'), ('x1', 'strict'), ('sx', 'strict + smooth'), ('c3k', 'chain3k'), ('c3', 'chain3'), ('v1', 'scan v1')]


def rows3(vs, variants):
    out = []
    for (k, l, gr, n) in VERS:
        if k not in R3 or (vs and k not in vs):
            continue
        for (vk, vl) in (VAR if variants else VAR[:1]):
            if vk in R3[k]:
                out.append((k + vk, '%s · %s' % (l, vl) if variants else l, gr, n if (not variants or vk == 'dd') else '', R3[k][vk]))
    return out


C3 = [
    ('Air F', g('air', 'F'), f_2, 'hi', 'Air Lab (vid1): air-frame F score'),
    ('Air hit /39', g('air', 'hit'), f_i, 'hi', 'true flights found'),
    ('Air false', g('air', 'false'), f_i, 'lo', 'flights invented (worse than missed)'),
    ('Dev hit /20', g('fl_dev', 'hit'), f_i, 'hi', 'Flight Lab dev'),
    ('Dev false', g('fl_dev', 'false'), f_i, 'lo', ''),
    ('Dev pos', flpos('dev'), f_1, None, 'median position error on the flights this version matched (n varies, so not comparable across rows)'),
    ('Hold hit /15', g('fl_hold', 'hit'), f_i, 'hi', 'Flight Lab hold-out'),
    ('Hold false', g('fl_hold', 'false'), f_i, 'lo', ''),
    ('Hold pos', flpos('hold'), f_1, None, ''),
    ('FL2 hit /28', g('fl2', 'hit'), f_i, 'hi', 'Flight Lab 2 (vid2)'),
    ('FL2 false', g('fl2', 'false'), f_i, 'lo', ''),
    ('FL2 P', g('fl2', 'P'), f_2, 'hi', 'air-frame precision'),
    ('FL2 spots', g('fl2', 'spots'), f_1, 'lo', 'landing / kick spot error (StatsBomb units)'),
    ('FL2 track', g('fl2', 'track'), f_1, 'lo', 'pitch path error (StatsBomb units)'),
    ('Fix wrong air', g('fix', 'wrong_air_runs'), f_i, 'lo', 'Fix Lab spans marked wrong that became flights'),
]

hist = '''<div class="tw"><table><caption>Earlier detectors (HANDOFF §5; weights deleted, can't be rerun)</caption>
<thead><tr><th class="v">Detector</th><th>Pass pts raw /16</th><th>Pass pts tracked /16</th></tr></thead><tbody>
<tr><td class="v"><b>YOLOv8n</b></td><td>2</td><td>4</td></tr>
<tr><td class="v"><b>White blobs</b></td><td>14</td><td>5</td></tr>
<tr><td class="v"><b>ballyolo @3840 / @2560</b></td><td>9 / 6</td><td>0–1</td></tr>
<tr><td class="v"><b>WASB whole frame</b></td><td>0</td><td>–</td></tr>
<tr><td class="v"><b>WASB 4×4 / 3×3 grid</b></td><td>6 / 6</td><td>1</td></tr>
</tbody></table></div>'''

body = open(os.path.join(HERE, 'report_head.html'), encoding='utf-8').read()
body = body.replace('{{T2A}}', table(C2a, rows2, '2D: hand-marked ball positions'))
body = body.replace('{{T2B}}', table(C2b, rows2, '2D: passes, Fix Lab, smoothness'))
body = body.replace('{{T3}}', table(C3, rows3(None, False), '3D: every version through the default flight chain'))
body = body.replace('{{T3V}}', table(C3, rows3(('hyb90', 'cls_B'), True),
                                     '3D: every flight model, ours vs hybrid'))
body = body.replace('{{HIST}}', hist)
open(os.path.join(HERE, 'report.html'), 'w', encoding='utf-8').write(body)
print('report.html', len(body))
