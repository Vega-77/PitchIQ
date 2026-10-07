"""Scoring player tracking against the player lab's hand labels.

The lab (`cv/balltrack/plab/`) shows forty keyframes, half a second apart, and
the user names every person in each: A1..An and B1..Bn for the two kits, AGK
and BGK for keepers, R for a referee, X for someone not playing, '-' for a box
that is not one person, and A?, B? or ? for a player whose identity they
can't tell. Missing players are added by tapping their feet. A confirmed
keyframe is complete truth; an unconfirmed one counts only for the labels the
user actually set.

What is measured, and why these:

* **coverage** — of the players on the pitch, how many the tracker has a box
  on. Recall over every labelled player, named or not.
* **precision** — of the boxes the tracker calls a player, how many are one.
  A box on a coach (X) or the referee (R) counts against it; a box on an
  unlabelled or '-' box is ignored, because nobody said what is there.
* **wrong-person rate** — the share of a tracker identity's appearances that
  belong to someone other than the player it mostly is. This is the number
  the user cares about most: a wrong identity silently gives one player's
  minutes, distance and touches to another, and nobody can see it happen.
* **IDF1** — the standard identity score: the best one-to-one pairing of
  labelled players with tracker identities, and how many appearances agree.
* **fragments** — tracker identities per labelled player; ID switches along
  each player's keyframes.
* **team accuracy**, **referee**, **identities per team** against the truth's.
* **position error** — feet in metres, when a projector is given, which says
  whether a box is on the right player and not merely near them.

Predictions are plain: for each keyframe frame, a list of (identity, box,
team, role). Every tracker — the shipped BoT-SORT, the pipeline's merged
clusters, the pitch tracker — is scored the same way.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .assignment import min_cost_assignment, min_cost_matching

NAMED = re.compile(r'^(A|B)(\d+|GK)$')
ANON = {'A?', 'B?', '?'}
REF = 'R'
NOT_PLAYING = 'X'
BAD = '-'

# Two labelled people whose feet are this close (fractions of box height) are
# "crowded": one stands in front of the other. In the lab a click there can
# land on either box, so the label is as unsure as the tracker; identity
# metrics are reported both with and without them.
CROWD_DX = 0.35
CROWD_DY = 0.25

GATE_FRACTION = 0.5     # feet within half a box height
GATE_MIN_PX = 12.0


@dataclass
class TruthEntry:
    x: float               # feet, pixels
    y: float
    h: float               # box height in pixels (estimated for added points)
    label: str
    crowded: bool = False  # another labelled person stands right here too


@dataclass
class Truth:
    frames: list[int]                          # keyframe frames scored
    entries: dict[int, list[TruthEntry]]
    confirmed: list[int]
    partial: list[int]

    @property
    def named(self) -> set[str]:
        return {e.label for es in self.entries.values() for e in es if NAMED.match(e.label)}


def _doc_body(obj: dict) -> dict:
    for key in ('data', 'doc', 'value'):
        if isinstance(obj.get(key), dict) and 'labels' not in obj:
            return obj[key]
    return obj


def load_docs(labels_dir) -> dict[int, dict]:
    """Keyframe docs from an ArtifactData listing (`kf/k07.json`) or a JSON dump."""
    labels_dir = Path(labels_dir)
    docs = {}
    paths = sorted(labels_dir.glob('k*.json')) + sorted((labels_dir / 'kf').glob('k*.json'))
    for p in paths:
        body = _doc_body(json.loads(p.read_text(encoding='utf-8')))
        k = body.get('k')
        if k is None:
            m = re.match(r'k(\d+)', p.stem)
            k = int(m.group(1)) if m else None
        if k is not None:
            docs[int(k)] = body
    for p in sorted(labels_dir.glob('*.json')):
        if re.match(r'k\d+$', p.stem):
            continue
        obj = json.loads(p.read_text(encoding='utf-8'))
        items = obj if isinstance(obj, list) else obj.get('docs') or obj.get('items') or []
        for item in items:
            body = _doc_body(item)
            if 'k' in body:
                docs.setdefault(int(body['k']), body)
    return docs


def _mark_crowded(entries: list[TruthEntry]) -> None:
    people = [e for e in entries if e.label not in ('', BAD)]
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            h = max(a.h, b.h)
            if abs(a.x - b.x) <= CROWD_DX * h and abs(a.y - b.y) <= CROWD_DY * h:
                a.crowded = b.crowded = True


def build_truth(sheets: dict, docs: dict[int, dict], include_partial: bool = True) -> Truth:
    """Truth entries per keyframe; partial keyframes keep only explicit labels."""
    entries: dict[int, list[TruthEntry]] = {}
    confirmed, partial = [], []
    heights = [b['xyxy'][3] - b['xyxy'][1] for s in sheets['sheets'] for b in s['boxes']]
    heights_y = [b['xyxy'][3] for s in sheets['sheets'] for b in s['boxes']]
    # added points have no box: give them the height boxes have at that row
    if len(heights) > 2 and np.ptp(heights_y) > 1.0:
        fit = np.polyfit(heights_y, heights, 1)
    else:
        fit = np.array([0.0, float(np.median(heights)) if heights else 60.0])
    for sheet in sheets['sheets']:
        k = int(sheet['k'])
        d = docs.get(k)
        if not d:
            continue
        done = bool(d.get('done'))
        if not done and not include_partial:
            continue
        labels = {int(i): v for i, v in (d.get('labels') or {}).items()}
        out = []
        for i, b in enumerate(sheet['boxes']):
            lab = labels.get(i)
            if lab is None:
                if done:
                    lab = ''
                else:
                    continue                    # a suggestion nobody checked
            x1, y1, x2, y2 = b['xyxy']
            out.append(TruthEntry((x1 + x2) / 2, y2, y2 - y1, str(lab)))
        for a in d.get('adds') or []:
            h = float(np.polyval(fit, a['y']))
            out.append(TruthEntry(float(a['x']), float(a['y']), max(h, 20.0), str(a.get('label') or '')))
        if not out:
            continue
        _mark_crowded(out)
        entries[int(sheet['f'])] = out
        (confirmed if done else partial).append(int(sheet['f']))
    return Truth(sorted(entries), entries, sorted(confirmed), sorted(partial))


@dataclass
class Pred:
    pid: object
    box: np.ndarray        # x1 y1 x2 y2
    team: str              # team_a / team_b / unknown
    role: str              # player / keeper / referee / off


def _is_player_label(label: str) -> bool:
    return bool(NAMED.match(label)) or label in ANON


def _label_team(label: str) -> str | None:
    if label and label[0] in 'AB' and (NAMED.match(label) or label in ('A?', 'B?')):
        return label[0]
    return None


@dataclass
class Score:
    name: str
    keyframes: int
    gt_players: int
    matched_players: int
    pred_claims: int
    false_claims: int
    idtp: int
    idfn: int
    idfp: int
    wrong_person: int
    named_matches: int
    fragments: dict = field(default_factory=dict)       # gt label -> distinct pred ids
    merges: dict = field(default_factory=dict)          # pred id -> distinct gt labels
    switches: int = 0
    team_right: int = 0
    team_total: int = 0
    ref_right: int = 0
    ref_total: int = 0
    keeper_right: int = 0
    keeper_total: int = 0
    pred_ids_by_team: dict = field(default_factory=dict)
    gt_ids_by_team: dict = field(default_factory=dict)
    pos_err_m: list = field(default_factory=list)
    unknown_team: int = 0

    @property
    def coverage(self) -> float:
        return self.matched_players / max(self.gt_players, 1)

    @property
    def precision(self) -> float:
        good = self.pred_claims - self.false_claims
        return good / max(self.pred_claims, 1)

    @property
    def idf1(self) -> float:
        return 2 * self.idtp / max(2 * self.idtp + self.idfp + self.idfn, 1)

    @property
    def wrong_person_rate(self) -> float:
        return self.wrong_person / max(self.named_matches, 1)

    @property
    def ids_per_player(self) -> float:
        return float(np.mean([len(v) for v in self.fragments.values()])) if self.fragments else float('nan')

    def row(self) -> dict:
        err = np.asarray(self.pos_err_m)
        return {
            'name': self.name,
            'kf': self.keyframes,
            'coverage': round(self.coverage, 3),
            'precision': round(self.precision, 3),
            'IDF1': round(self.idf1, 3),
            'wrong_person': round(self.wrong_person_rate, 3),
            'merged_ids': sum(len(v) > 1 for v in self.merges.values()),
            'ids_per_player': round(self.ids_per_player, 2),
            'switches': self.switches,
            'team_acc': round(self.team_right / max(self.team_total, 1), 3),
            'ref_ok': '%d/%d' % (self.ref_right, self.ref_total),
            'gk_ok': '%d/%d' % (self.keeper_right, self.keeper_total),
            'ids_A/B': '%s/%s vs %s/%s' % (
                self.pred_ids_by_team.get('A', 0), self.pred_ids_by_team.get('B', 0),
                self.gt_ids_by_team.get('A', 0), self.gt_ids_by_team.get('B', 0)),
            'pos_m_med': round(float(np.median(err)), 2) if len(err) else None,
            'pos_m_p90': round(float(np.percentile(err, 90)), 2) if len(err) else None,
        }


def _match_frame(entries: list[TruthEntry], preds: list[Pred]):
    """(entry index, pred index) pairs: scored truth first, then ignore regions."""
    feet = np.array([[(p.box[0] + p.box[2]) / 2, p.box[3]] for p in preds]).reshape(-1, 2)

    def cost(idx):
        c = np.full((len(idx), len(preds)), np.inf)
        for a, i in enumerate(idx):
            e = entries[i]
            gate = max(GATE_FRACTION * e.h, GATE_MIN_PX)
            d = np.hypot(feet[:, 0] - e.x, feet[:, 1] - e.y)
            ok = d <= gate
            c[a, ok] = d[ok] / gate
        return c

    scored = [i for i, e in enumerate(entries) if e.label not in ('', BAD)]
    ignore = [i for i, e in enumerate(entries) if e.label in ('', BAD)]
    pairs = [(scored[a], b) for a, b in min_cost_matching(cost(scored), unmatched=1.0)] if scored and preds else []
    used = {b for _, b in pairs}
    rest = [b for b in range(len(preds)) if b not in used]
    ignored = set()
    if ignore and rest:
        c = cost(ignore)[:, rest]
        for a, b in min_cost_matching(c, unmatched=1.0):
            ignored.add(rest[b])
    return pairs, ignored


def score(name: str, truth: Truth, predict, projector=None, clear_only: bool = False) -> Score:
    """Score `predict(frame) -> list[Pred]` on every truth keyframe.

    `clear_only` leaves crowded labels out of the identity metrics (IDF1,
    wrong person, switches, fragments); coverage and precision count them.
    """
    sc = Score(name, len(truth.frames), 0, 0, 0, 0, 0, 0, 0, 0, 0)
    co: dict[tuple, int] = {}                 # (gt label, pred id) -> appearances together
    gt_n: dict[str, int] = {}
    seq: dict[str, list] = {}
    team_votes = np.zeros((2, 2))             # [gt A/B, pred team_a/team_b]
    pred_named_n: dict = {}
    pred_ids_team: dict[str, set] = {}
    claims_unmatched = 0

    for f in truth.frames:
        entries = truth.entries[f]
        preds = predict(f)
        pairs, ignored = _match_frame(entries, preds)
        by_pred = {b: a for a, b in pairs}
        for e in entries:
            if _is_player_label(e.label):
                sc.gt_players += 1
            if NAMED.match(e.label) and not (clear_only and e.crowded):
                gt_n[e.label] = gt_n.get(e.label, 0) + 1
        for b, p in enumerate(preds):
            claims = p.role in ('player', 'keeper')
            if b in ignored:
                continue
            if claims:
                sc.pred_claims += 1
            if b not in by_pred:
                if claims:
                    sc.false_claims += 1
                    claims_unmatched += 1
                continue
            e = entries[by_pred[b]]
            if _is_player_label(e.label):
                sc.matched_players += claims
            elif claims:
                sc.false_claims += 1                 # a box on a coach or the referee
                claims_unmatched += 1
            if e.label == REF:
                sc.ref_total += 1
                sc.ref_right += p.role == 'referee'
            if e.label in ('AGK', 'BGK'):
                sc.keeper_total += 1
                sc.keeper_right += p.role == 'keeper'
            if NAMED.match(e.label) and claims and not (clear_only and e.crowded):
                co[(e.label, p.pid)] = co.get((e.label, p.pid), 0) + 1
                seq.setdefault(e.label, []).append(p.pid)
                pred_named_n[p.pid] = pred_named_n.get(p.pid, 0) + 1
            gt_team = _label_team(e.label)
            if gt_team and claims and not e.label.endswith('GK'):
                if p.team in ('team_a', 'team_b'):
                    team_votes['AB'.index(gt_team), ('team_a', 'team_b').index(p.team)] += 1
                else:
                    sc.unknown_team += 1
                sc.team_total += 1
            if projector is not None and _is_player_label(e.label) and projector.has(f):
                g = projector.to_m(f, [[e.x, e.y]])[0]
                q = projector.to_m(f, [[(p.box[0] + p.box[2]) / 2, p.box[3]]])[0]
                sc.pos_err_m.append(float(np.hypot(*(g - q))))

    # team names are arbitrary: map team_a to whichever of A/B it mostly is
    straight = team_votes[0, 0] + team_votes[1, 1]
    crossed = team_votes[0, 1] + team_votes[1, 0]
    flip = crossed > straight
    sc.team_right = int(max(straight, crossed))
    mapping = {'team_a': 'B' if flip else 'A', 'team_b': 'A' if flip else 'B'}

    # identity: best one-to-one between named players and pred ids
    gts = sorted(gt_n)
    pids = sorted({pid for _, pid in co}, key=str)
    if gts and pids:
        W = np.zeros((len(gts), len(pids)))
        for (g, pid), n in co.items():
            W[gts.index(g), pids.index(pid)] = n
        C = np.where(W > 0, -W, np.inf)
        pairs = min_cost_assignment(C)
        sc.idtp = int(sum(W[i, j] for i, j in pairs))
    sc.idfn = sum(gt_n.values()) - sc.idtp
    sc.idfp = sum(pred_named_n.values()) + claims_unmatched - sc.idtp

    for g in gts:
        sc.fragments[g] = sorted({pid for (gg, pid) in co if gg == g}, key=str)
        s = seq.get(g, [])
        sc.switches += sum(1 for a, b in zip(s, s[1:]) if a != b)
    for pid in pids:
        mine = {g: n for (g, p), n in co.items() if p == pid}
        sc.merges[pid] = sorted(mine)
        sc.wrong_person += sum(mine.values()) - max(mine.values())
    sc.named_matches = sum(pred_named_n.values())

    pred_team: dict = {}
    for f in truth.frames:
        for p in predict(f):
            if p.role == 'player' and p.team in mapping:
                pred_team[p.pid] = mapping[p.team]
    for pid in {pid for (_, pid) in co}:
        t = pred_team.get(pid)
        if t:
            pred_ids_team.setdefault(t, set()).add(pid)
    sc.pred_ids_by_team = {t: len(v) for t, v in pred_ids_team.items()}
    gt_team_ids: dict[str, set] = {}
    for g in gts:
        if not g.endswith('GK'):
            gt_team_ids.setdefault(g[0], set()).add(g)
    sc.gt_ids_by_team = {t: len(v) for t, v in gt_team_ids.items()}
    return sc


def format_table(rows: list[dict]) -> str:
    if not rows:
        return '(nothing scored)'
    keys = list(rows[0])
    width = {k: max(len(k), *(len(str(r.get(k))) for r in rows)) for k in keys}
    head = '  '.join(k.ljust(width[k]) for k in keys)
    lines = [head, '-' * len(head)]
    for r in rows:
        lines.append('  '.join(str(r.get(k)).ljust(width[k]) for k in keys))
    return '\n'.join(lines)
