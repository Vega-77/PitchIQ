"""The ball's path on the pitch over the 5-minute window, one panel per minute.

  thin line, coloured by time   ball on the ground (breaks where it is not seen)
  thick orange, arrow at end    ball in the air (3D flight)

  dashed yellow                 a flight you marked in the Flight Lab (take-off -> landing), when there are labels

    python vpathplot.py [ver] [tag] -> path_<ver>.png (vid1) or <tag>_path_<ver>.png
"""
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import vovl

ver = sys.argv[1] if len(sys.argv) > 1 else 'd'
tag = sys.argv[2] if len(sys.argv) > 2 else 'vid1'
B = np.load(os.path.join(HERE, '%s_ball3d%s.npz' % (tag, ver)))
LAB = {'vid2': 'flightlab2_labels.json'}.get(tag)
MARKED = []
if LAB:
    import json
    MARKED = [f for f in json.load(open(os.path.join(HERE, LAB)))['flights'] if f.get('p0') and f.get('p1')]
ab, x, y, st = B['abs'], B['x'], B['y'], B['state']
A0 = int(ab[0]); n = len(ab)
air = st == 'air'
GAP = 10


def clock(f):
    s = f / 30
    return '%d:%02d' % (s // 60, s % 60)


def pitch(ax):
    ax.add_patch(plt.Rectangle((-6, -6), 132, 92, color='#2f6b35', zorder=0))
    for pl in vovl.polylines():
        ax.plot(pl[:, 0], pl[:, 1], color='white', lw=0.8, zorder=1)
    ax.set_xlim(-6, 126); ax.set_ylim(-6, 86); ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])


def runs(mask):
    out = []; i = 0
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j)); i = j + 1
        else:
            i += 1
    return out


def draw(ax, lo, hi, cmap, norm):
    ok = np.isfinite(x) & (x > -6) & (x < 126) & (y > -6) & (y < 86)
    # ground: segments between consecutive seen frames (no bridge over long gaps)
    g = np.where(ok & ~air)[0]; g = g[(g >= lo) & (g <= hi)]
    seg, col = [], []
    for a, b in zip(g[:-1], g[1:]):
        if b - a <= GAP:
            seg.append([(x[a], y[a]), (x[b], y[b])]); col.append(a)
    lc = LineCollection(seg, cmap=cmap, norm=norm, linewidths=1.2, zorder=2)
    lc.set_array(np.array(col, float)); ax.add_collection(lc)
    for a, b in runs(air):
        if b < lo or a > hi:
            continue
        k = np.arange(max(a, lo), min(b, hi) + 1); k = k[ok[k]]
        if len(k) < 2:
            continue
        ax.plot(x[k], y[k], color='#ff8c1a', lw=2.6, zorder=3, solid_capstyle='round')
        ax.annotate('', xy=(x[k[-1]], y[k[-1]]), xytext=(x[k[-2]], y[k[-2]]),
                    arrowprops=dict(arrowstyle='-|>', color='#ff8c1a', lw=2, mutation_scale=14), zorder=4)
    for f in MARKED:
        if lo <= f['f0'] - A0 <= hi:
            ax.plot([f['p0']['x'], f['p1']['x']], [f['p0']['y'], f['p1']['y']], color='#ffe14d', lw=1.4, ls=(0, (4, 3)), zorder=5)
            ax.plot(f['p1']['x'], f['p1']['y'], 'o', color='#ffe14d', ms=3.5, zorder=5)
    return lc


def main():
    cmap = plt.get_cmap('viridis')
    fig = plt.figure(figsize=(16, 27), facecolor='white')
    gs = fig.add_gridspec(4, 2, height_ratios=[2.05, 1, 1, 1], hspace=0.12, wspace=0.05)
    ax = fig.add_subplot(gs[0, :])
    pitch(ax); ax.set_anchor('C')
    norm = plt.Normalize(0, n - 1)
    lc = draw(ax, 0, n - 1, cmap, norm)
    cb = fig.colorbar(lc, ax=ax, fraction=0.025, pad=0.01)
    ticks = np.arange(0, n, 1800); cb.set_ticks(ticks); cb.set_ticklabels([clock(A0 + t) for t in ticks])
    ax.set_title('Ball path, match %s - %s  (line colour = time on the ground, orange = in the air)'
                 % (clock(A0), clock(A0 + n - 1)), fontsize=13)
    ax.text(60, -4.6, 'camera side', ha='center', color='white', fontsize=9)
    per = 1800
    for q in range(5):
        a_ = fig.add_subplot(gs[1 + q // 2, q % 2])
        pitch(a_)
        lo, hi = q * per, min(n - 1, (q + 1) * per - 1)
        draw(a_, lo, hi, cmap, plt.Normalize(lo, hi))
        na = sum(1 for a, b in runs(air) if lo <= a <= hi)
        a_.set_title('%s - %s   (%d flights)' % (clock(A0 + lo), clock(A0 + hi), na), fontsize=11)
    ax6 = fig.add_subplot(gs[3, 1]); ax6.axis('off')
    ax6.text(0.02, 0.8, 'How to read it', fontsize=12, weight='bold')
    ax6.text(0.02, 0.2, 'Pitch is 120 x 80, camera touchline at the bottom.\n'
             'Thin line: ball on the ground, dark = earlier, yellow = later\n(in each minute panel the colours restart).\n'
             'Orange with arrow: a fitted flight, drawn where the ball is\nover the grass, not where the flat reading would put it.\n'
             'Gaps: the tracker has no ball (dead ball, hidden, or unsure).', fontsize=10.5, va='bottom')
    if MARKED:
        ax6.text(0.02, 0.02, 'Dashed yellow: a flight you marked (take-off -> landing dot).\n'
                 '%d marked with both ends drawn; %d fitted flights (one can span two).' % (len(MARKED), len(runs(air))), fontsize=10.5, va='bottom', color='#8a6d00')
    out = os.path.join(HERE, ('path_%s.png' % ver) if tag == 'vid1' else ('%s_path_%s.png' % (tag, ver)))
    fig.savefig(out, dpi=90, bbox_inches='tight')
    print(out, os.path.getsize(out) // 1024, 'KB')


if __name__ == '__main__':
    main()
