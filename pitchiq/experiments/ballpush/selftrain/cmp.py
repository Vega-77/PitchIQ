"""Per-half replay counts of score folders side by side (reads selftrain/replay/replay_<name>.json).

    python cmp.py oof_cls <name> [<name> ...]     names = basename of the folder given to replay.py
"""
import json
import sys

import st


def counts(name):
    per = json.load(open(st.OUT + '/replay/replay_%s.json' % name))['per']['base']
    out = {}
    for r in per.values():
        c = out.setdefault(r['half'], dict(right=0, wrong=0, out=0, missed=0))
        if r['v'] == 'ball':
            c['right' if r['right'] else ('wrong' if r['ring'] else 'missed')] += 1
        else:
            c['out'] += r['ring']
    return out


def main():
    names = sys.argv[1:]
    C = {n: counts(n) for n in names}
    halves = [t for t in st.DEV if all(t in C[n] for n in names)]
    print('%-8s' % 'half', '   '.join('%-22s' % n[:22] for n in names), '  (right / wrong on ball / out rings / missed)')
    tot = {n: [0, 0, 0, 0] for n in names}
    for t in halves:
        row = []
        for n in names:
            c = C[n][t]
            v = [c['right'], c['wrong'], c['out'], c['missed']]
            tot[n] = [a + b for a, b in zip(tot[n], v)]
            row.append('%3d /%3d /%2d /%3d      ' % tuple(v))
        print('%-8s' % t, '   '.join(row))
    print('%-8s' % 'ALL', '   '.join('%3d /%3d /%2d /%3d      ' % tuple(tot[n]) for n in names))
    for n in names:
        r, w, o, m = tot[n]
        print('%-22s right %d  wrong %d (ball %d + out %d)  missed %d  | missed share %.3f  accuracy %.3f' % (
            n, r, w + o, w, o, m, m / max(1, r + w + m), r / max(1, r + w + o)))


if __name__ == '__main__':
    main()
