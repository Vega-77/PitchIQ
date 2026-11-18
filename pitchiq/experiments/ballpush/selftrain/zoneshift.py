"""Add a constant to the calibrated scores of zone candidates (after train.py finish, so the global calibration
cannot undo it).  feetmerge.py --shift is applied before finish, and finish's label-free fit on frame tops
pulls a zone shift back down (kz1m_s1: first & p > 2 fell 264 -> 262).

    python zoneshift.py <src cls dir> <dst run name> --shift 1 [--mode player|feet] [--near 0.1]
      -> runs/<dst>_global/<piece>cls.npz ; prints the blob table against the source
"""
import argparse
import os

import numpy as np

import st
import train as T1
import train3 as T3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--shift', type=float, required=True)
    ap.add_argument('--mode', default='player')
    ap.add_argument('--near', type=float, default=0.1)
    a = ap.parse_args()
    _, names, _ = T1.base_data()
    out = st.OUT + '/runs/%s_global' % a.dst
    os.makedirs(out, exist_ok=True)
    n = 0
    for pt in st.pieces():
        if not os.path.exists(os.path.join(a.src, '%scls.npz' % pt)):
            continue
        f, j, p = st.load_oof(pt, a.src)
        ca = st.load_cache(pt, crops=False)
        z = T3.feet_zone(ca['ctx'], names, a.near, a.mode)
        p = p.copy()
        rows = np.searchsorted(f * 64 + j, ca['f'] * 64 + ca['j'])
        p[rows[z]] += a.shift
        n += int(z.sum())
        np.savez(out + '/%scls.npz' % pt, f=f.astype(np.int32), j=j.astype(np.int16), p=p.astype(np.float32))
    print('shifted %d zone candidates by %+g -> %s' % (n, a.shift, out))
    T1.print_rank(T1.rank_table({'src': a.src, 'shifted': out}))


if __name__ == '__main__':
    main()
