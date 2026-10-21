"""A handful of fixed gap_fill settings, each scored on both halves - no
tuning, so no split to overfit.  Wrong picks are split into those the
fill added and those the tracker already made.

    python -m pitchiq.balltrack.gap_fill_settings
"""

from . import ball_tracker
from . import gap_fill
from . import wm_cls
from . import wm_tune

BASE = dict(off_score=-0.5, enter_cost=12.0, return_cost=6.0, skip_cost=4.0)
A = 0.75
FILLS = [
    ('none', None),
    ('gap16 r10+2', dict(maxgap=16, rad0=10.0, rgrow=2.0)),
    ('gap32 r10+2', dict(maxgap=32, rad0=10.0, rgrow=2.0)),
    ('gap32 r20+5', dict(maxgap=32, rad0=20.0, rgrow=5.0)),
    ('gap64 r20+5', dict(maxgap=64, rad0=20.0, rgrow=5.0)),
    ('gap32 r20+5 ext2', dict(maxgap=32, rad0=20.0, rgrow=5.0, ext=2)),
    ('gap32 r10+2 syn', dict(maxgap=32, rad0=10.0, rgrow=2.0, synth=1)),
    ('gap16 r10+2 syn', dict(maxgap=16, rad0=10.0, rgrow=2.0, synth=1)),
    ('gap32 r20+5 syn', dict(maxgap=32, rad0=20.0, rgrow=5.0, synth=1)),
]


def main():
    T, tags, F, idx = wm_tune.load_all()
    for tag in tags:
        wm_cls.attach(F[tag], tag)
        gap_fill.attach_pan(F[tag], tag)
        for fr in F[tag]:
            fr['s'] = A * fr['p'] + (1 - A) * fr['s0']
    base = {
        tag: ball_tracker.track_ball(F[tag], return_drift=12.0, return_gate=2.0, **BASE)
        for tag in tags
    }
    halves = {'H1': {t for t in tags if int(t[2:]) < 9}, 'H2': {t for t in tags if int(t[2:]) >= 9}}
    for name, fc in FILLS:
        p = base if fc is None else {t: gap_fill.fill(F[t], base[t], **fc) for t in tags}
        print('%-18s' % name)
        for h in ('H1', 'H2'):
            r = gap_fill.score(T, F, idx, p, halves[h])
            print('   %s %s  syn +%d/-%d' % (h, wm_tune.fmt(r), r['syn_right'], r['syn_wrong']))


if __name__ == '__main__':
    main()
