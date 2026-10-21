import json

import numpy as np

from cv.player_eval import Pred, build_truth, load_docs, score


def sheet(k, f, feet):
    """A lab sheet: boxes 60 px tall standing on the given feet points."""
    return {
        'k': k,
        'f': f,
        'boxes': [{'xyxy': [x - 15, y - 60, x + 15, y], 'tid': i} for i, (x, y) in enumerate(feet)],
    }


FEET = [(100, 500), (300, 500), (500, 500), (700, 500)]
SHEETS = {'sheets': [sheet(0, 1000, FEET), sheet(1, 1015, FEET), sheet(2, 1030, FEET)]}
LABELS = {'0': 'A1', '1': 'A2', '2': 'B1', '3': 'R'}


def docs(n=3, **extra):
    return {
        k: dict(
            {'k': k, 'f': 1000 + 15 * k, 'done': True, 'labels': dict(LABELS), 'adds': []}, **extra
        )
        for k in range(n)
    }


def predictor(table):
    return lambda f: table.get(f, [])


def perfect(frames=(1000, 1015, 1030), teams=('team_a', 'team_a', 'team_b'), ids=(1, 2, 3)):
    out = {}
    for f in frames:
        out[f] = [
            Pred(ids[i], np.array([x - 15, y - 60, x + 15, y], float), teams[i], 'player')
            for i, (x, y) in enumerate(FEET[:3])
        ]
        x, y = FEET[3]
        out[f].append(Pred(99, np.array([x - 15, y - 60, x + 15, y], float), 'unknown', 'referee'))
    return out


def test_load_docs_reads_listing_folders_and_dumps(tmp_path):
    (tmp_path / 'kf').mkdir()
    (tmp_path / 'kf' / 'k00.json').write_text(json.dumps({'k': 0, 'done': True, 'labels': {}}))
    (tmp_path / 'dump.json').write_text(json.dumps([{'k': 3, 'labels': {'0': 'A1'}}]))
    got = load_docs(tmp_path)
    assert sorted(got) == [0, 3]


def test_done_and_partial_keyframes():
    d = docs()
    d[1] = {'k': 1, 'done': False, 'labels': {'0': 'A1'}}  # one box checked so far
    d[2]['adds'] = [{'label': 'B2', 'x': 900, 'y': 500}]
    t = build_truth(SHEETS, d)
    assert t.confirmed == [1000, 1030] and t.partial == [1015]
    assert [e.label for e in t.entries[1015]] == ['A1']  # unchecked boxes are left out
    added = t.entries[1030][-1]
    assert added.label == 'B2' and abs(added.h - 60) < 1  # height from the sheet's boxes
    assert t.named == {'A1', 'A2', 'B1', 'B2'}
    assert build_truth(SHEETS, d, include_partial=False).frames == [1000, 1030]
    # in a finished keyframe a box with no label is an unknown, not dropped
    d[0]['labels'].pop('2')
    assert [e.label for e in build_truth(SHEETS, d).entries[1000]] == ['A1', 'A2', '', 'R']


def test_perfect_prediction_scores_perfectly():
    sc = score('p', build_truth(SHEETS, docs()), predictor(perfect()))
    r = sc.row()
    assert r['coverage'] == 1.0 and r['precision'] == 1.0 and r['IDF1'] == 1.0
    assert r['wrong_person'] == 0 and r['switches'] == 0 and r['merged_ids'] == 0
    assert r['team_acc'] == 1.0 and r['ref_ok'] == '3/3'
    assert r['ids_per_player'] == 1.0


def test_team_names_are_arbitrary():
    swapped = perfect(teams=('team_b', 'team_b', 'team_a'))
    assert score('p', build_truth(SHEETS, docs()), predictor(swapped)).row()['team_acc'] == 1.0


def test_a_swap_is_a_wrong_person_and_a_switch():
    table = perfect()
    a1, a2 = table[1030][0], table[1030][1]
    a1.pid, a2.pid = a2.pid, a1.pid  # A1 and A2 swap in the last keyframe
    r = score('p', build_truth(SHEETS, docs()), predictor(table)).row()
    assert r['IDF1'] < 1.0
    assert r['wrong_person'] > 0
    assert r['switches'] == 2
    assert r['merged_ids'] == 2


def test_a_new_id_is_a_fragment_not_a_wrong_person():
    table = perfect()
    table[1030][0].pid = 42
    r = score('p', build_truth(SHEETS, docs()), predictor(table)).row()
    assert r['wrong_person'] == 0 and r['merged_ids'] == 0
    assert r['ids_per_player'] > 1 and r['switches'] == 1


def test_claiming_a_non_player_is_a_false_claim():
    d = docs()
    for doc in d.values():
        doc['labels']['1'] = 'X'  # a coach, not a player
    r = score('p', build_truth(SHEETS, d), predictor(perfect())).row()
    assert r['precision'] < 1.0
    assert r['coverage'] == 1.0


def test_unknown_and_bad_boxes_are_ignored():
    d = docs()
    for doc in d.values():
        doc['labels']['1'] = ''
        doc['labels']['2'] = '-'
    r = score('p', build_truth(SHEETS, d), predictor(perfect())).row()
    assert r['precision'] == 1.0 and r['coverage'] == 1.0


def test_missing_a_player_costs_coverage():
    table = perfect()
    for f in table:
        table[f] = table[f][1:]
    r = score('p', build_truth(SHEETS, docs()), predictor(table)).row()
    assert r['coverage'] == round(2 / 3, 3)
    assert r['precision'] == 1.0


def test_a_box_far_from_the_feet_does_not_match():
    table = perfect()
    for f in table:
        table[f][0].box = table[f][0].box + np.array([0, 80, 0, 80])  # well over half a box low
    r = score('p', build_truth(SHEETS, docs()), predictor(table)).row()
    assert r['coverage'] < 1.0 and r['precision'] < 1.0
