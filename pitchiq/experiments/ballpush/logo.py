"""Leave-one-game-out choice among replayed variants.

    python logo.py <replay json> <family prefix or comma list of variants> [wrong weight, default 2]

For each of the 8 dev games: pick the variant (base always among them) with the best
right - W * wrong over the other 7 games, score the held game with it; sum the 8 folds.
Prints every variant's in-sample totals, the in-sample best, the leave-one-game-out total and the
per-half table of the in-sample best.
"""
import json
import sys

GAME = dict(nb1='nb', ww1='ww', ww2='ww', eb1='eb', eb2='eb', scrimh1='scrim', scrimh2='scrim', wh2='wh', wb1='wb', pi2='pi', co2='co')
HALVES = list(GAME)


def counts(per, keep=None):
    c = dict(right=0, wrong_ball=0, out_ring=0, missed=0)
    for iid, r in per.items():
        if keep is not None and not keep(r['half']):
            continue
        if r['v'] == 'ball':
            c['right'] += r['right']
            c['wrong_ball'] += r['ring'] and not r['right']
            c['missed'] += not r['ring']
        else:
            c['out_ring'] += r['ring']
    return c


def fmt(c):
    w = c['wrong_ball'] + c['out_ring']
    return 'right %3d  wrong on ball %2d  out rings %2d  (wrong %2d)  missed %3d' % (c['right'], c['wrong_ball'], c['out_ring'], w, c['missed'])


def main():
    d = json.load(open(sys.argv[1]))['per']
    sel = sys.argv[2]
    W = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    names = [n for n in d if n == 'base' or (n in sel.split(',') if ',' in sel else n.startswith(sel))]

    def obj(c):
        return c['right'] - W * (c['wrong_ball'] + c['out_ring'])
    print('objective right - %g * wrong; variants: %s' % (W, ' '.join(names)))
    for n in names:
        print('  %-24s %s' % (n, fmt(counts(d[n]))))
    best = max(names, key=lambda n: (obj(counts(d[n])), n == 'base'))
    tot = dict(right=0, wrong_ball=0, out_ring=0, missed=0)
    for g in sorted(set(GAME.values())):
        pick = max(names, key=lambda n: (obj(counts(d[n], lambda h: GAME[h] != g)), n == 'base'))
        c = counts(d[pick], lambda h: GAME[h] == g)
        b = counts(d['base'], lambda h: GAME[h] == g)
        print('  fold %-6s picks %-22s held game: %s   (base right %d wrong %d)' % (g, pick, fmt(c), b['right'], b['wrong_ball'] + b['out_ring']))
        for k in tot:
            tot[k] += c[k]
    print('IN-SAMPLE BEST  %-20s %s' % (best, fmt(counts(d[best]))))
    print('LEAVE-ONE-GAME-OUT               %s' % fmt(tot))
    print('BASE                             %s' % fmt(counts(d['base'])))
    print('per half, %s vs base:' % best)
    for h in HALVES:
        c, b = counts(d[best], lambda x: x == h), counts(d['base'], lambda x: x == h)
        print('  %-8s %s   | base %s' % (h, fmt(c), fmt(b)))


if __name__ == '__main__':
    main()
