"""missbreak.py on the held-out halves: COUNTS ONLY (no frames viewed, nothing tuned).  A held-out look; log it.

    python missbreak_held.py <export dir (tnclick score EXPORT, TN_SET=held)> <held scores dir (held/runs/<run>/cls)>
"""
import json
import os

os.environ['ST_HELD'] = '1'

import st  # noqa: E402
import missbreak  # noqa: E402


def clicks():
    st.common.point_at_data()
    from pitchiq.experiments.clicker import clickscore as cs

    items = {it['id']: it for it in json.load(open(os.path.join(cs.ROOT, 'items_all.json')))['items']}
    A = cs.answers(os.path.join(cs.ROOT, 'db'))
    out = {t: [] for t in st.DEV}
    for i, a in A.items():
        it = items.get(i)
        if it and it['half'] in st.DEV and a['v'] in ('ball', 'out'):
            out[it['half']].append((i, int(it['f']), a))
    return out


st.clicks = clicks

if __name__ == '__main__':
    missbreak.main()
