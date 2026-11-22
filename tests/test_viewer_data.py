"""The match viewer's export (session step 3.9): samples, chunks and the moments' why."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import numpy as np
import pytest

from pitchiq import viewer_data as vd
from pitchiq.cli import viewer_export

FPS = 30.0
L, W = 100.0, 60.0
LIGHT, DARK = [200.0, 128.0, 128.0], [60.0, 150.0, 150.0]


def write_map(path, f0, frames, light_team=0):
    """Three dots a frame: map trk 1 (team 0), trk 2 (team 1) walking along, and a grey
    referee (trk 3) standing still; trk 2 is a guess on odd frames."""
    rows = {k: [] for k in ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role', 'slot')}
    for f in range(f0, f0 + frames):
        k = f - f0
        for trk, team, role, x, y in ((1, 0, 0, 10 + k * 0.1, 20), (2, 1, 0, 50, 30 + k * 0.05),
                                      (3, 2, 2, 40, 40)):  # fmt: skip
            rows['f'].append(f)
            rows['trk'].append(trk)
            rows['x'].append(x)
            rows['y'].append(y)
            rows['seen'].append(not (trk == 2 and k % 2))
            rows['alpha'].append(0.5)
            rows['team'].append(team)
            rows['role'].append(role)
            rows['slot'].append(0)
    kits = [LIGHT, DARK] if light_team == 0 else [DARK, LIGHT]
    np.savez(
        path,
        **{k: np.array(v) for k, v in rows.items()},
        fps=FPS,
        length_m=L,
        width_m=W,
        kit_lab=np.array([kits, kits]),
    )


def write_ball(path, f0, frames):
    """The ball at trk 1's feet; in the air (half a camera height) for frames 30-59."""
    k = np.arange(frames)
    state = np.where((k >= 30) & (k < 60), 'air', 'ground').astype('<U8')
    state[:6] = 'none'
    x = (10 + k * 0.1) * 120.0 / L
    y = np.full(frames, 20 * 80.0 / W)
    z = np.where(state == 'air', 0.5, 0.0)
    np.savez(path, abs=f0 + k, x=x, y=y, z=z, state=state)


def write_holds(path, f0, frames):
    states = [
        SimpleNamespace(
            timestamp_s=(f0 + k) / FPS,
            team='team_a' if k < 60 else 'dead',
            holder_track=1 * 3 + 0 if k < 60 else None,
        )  # fmt: skip
        for k in range(frames)
    ]
    vd.save_holds(states, FPS, path)


@pytest.fixture
def game(tmp_path):
    halves = []
    for i, f0 in enumerate((300, 3000)):
        tag = f'xy{i + 1}'
        write_map(tmp_path / f'{tag}.npz', f0, 90)
        write_ball(tmp_path / f'{tag}_ball.npz', f0, 90)
        write_holds(tmp_path / f'{tag}.holds.npz', f0, 90)
        halves.append(
            vd.HalfInputs(
                tag,
                tmp_path / f'{tag}.npz',
                tmp_path / f'{tag}_ball.npz',
                tmp_path / f'{tag}.holds.npz',
                camera_height_m=10.0,
            )  # fmt: skip
        )
    return halves


def arr(chunk, key, dtype):
    return np.frombuffer(base64.b64decode(chunk[key]), np.dtype(dtype).newbyteorder('<'))


def moment(frame, track, team='team_a', start=None):
    return dict(type='pass', frame_index=frame, timestamp_s=frame / FPS, team=team,
                track_id=track, start_m=start, outcome='complete')  # fmt: skip


def test_ours_is_the_lighter_or_darker_kit():
    kits = np.array([[LIGHT, DARK]] * 3)
    assert vd.ours_team(kits, 'lighter') == 0
    assert vd.ours_team(kits, 'darker') == 1
    assert vd.ours_team(kits[:, ::-1], 'lighter') == 1
    assert vd.ours_team(kits, '1') == 1


def test_samples_pack_dots_ball_and_holder(game):
    index, chunks = vd.build('xy', game, {'events': []}, 'lighter')
    assert [h['step'] for h in index['halves']] == [6, 6]
    c = chunks[0]
    assert c['tag'] == 'xy1' and c['f0'] == 300 and c['samples'] == 15  # 90 frames, every 6th
    n = arr(c, 'n', np.uint8)
    assert n.tolist() == [3] * 15
    x, y = arr(c, 'x', np.int16), arr(c, 'y', np.int16)
    ids, code = arr(c, 'id32', np.uint32), arr(c, 'code', np.uint8)
    # sample 2 is frame 312: trk 1 at x = 10 + 1.2 m
    assert x[6] == 112 and y[6] == 200 and ids[6] == 3 * 1 + 0
    assert (code[6] & 3) == 0 and (code[7] & 3) == 1 and (code[8] & 3) == 2  # a, b, grey
    assert (code[8] >> 2) & 3 == 2  # the referee's role
    assert code[6] >> 4 == vd.SEEN
    assert code[4] >> 4 == vd.SEEN  # trk 2 on frame 306 (even): seen
    bs, bh = arr(c, 'bs', np.uint8), arr(c, 'bh', np.uint8)
    assert vd.BALL_STATES[bs[0]] == 'none' and arr(c, 'bx', np.int16)[0] == vd.NO_XY
    assert vd.BALL_STATES[bs[5]] == 'air' and bh[5] == 50  # 0.5 camera heights of 10 m, in dm
    assert vd.BALL_STATES[bs[1]] == 'ground' and arr(c, 'bx', np.int16)[1] == 106
    ht, hid = arr(c, 'ht', np.uint8), arr(c, 'hid', np.uint32)
    assert ht[:10].tolist() == [0] * 10 and ht[10:].tolist() == [3] * 5  # team_a, then dead
    assert hid[0] == 3 and hid[10] == vd.NO_ID


def test_a_guess_keeps_its_alpha(game):
    # every sample frame is even (step 6 from 300), so look at a full-rate odd frame
    h = vd.load_half(game[0], 1, 'lighter')
    odd = np.flatnonzero((h.f == 301) & (h.trk_id == 2 * 3 + 1))[0]
    assert h.code[odd] >> 4 == 7  # alpha 0.5 of 14


def test_darker_ours_swaps_the_teams(game):
    index, chunks = vd.build('xy', game, {'events': []}, 'darker')
    code = arr(chunks[0], 'code', np.uint8)
    assert (code[0] & 3) == 1 and (code[1] & 3) == 0
    assert index['kits']['team_a'] == vd.kit_hex(DARK)


def test_moments_carry_their_why(game):
    report = {'events': [moment(312, 3, start=[11.2, 20.0]), moment(3012, 3), moment(99, 3)]}
    index, _ = vd.build('xy', game, report, 'lighter')
    ms = index['moments']
    assert len(ms) == 2  # frame 99 is in no half
    m = ms[0]
    assert m['p'] == 1 and m['t'] == pytest.approx(10.4) and m['team'] == 'a' and m['id'] == 3
    assert m['why']['d'] == 0.0 and m['why']['ball'] == 'ground'
    # the nearest other player is trk 2 (the referee is not a player)
    assert m['why']['near'] == 2 * 3 + 1 and m['why']['near_team'] == 'b'
    assert m['why']['near_d'] == pytest.approx(np.hypot(50 - 11.2, 30.6 - 20), abs=0.05)
    assert ms[1]['p'] == 2 and ms[1]['why']['d'] == 0.0  # no start: the 3D ball's place
    assert index['team_mismatch'] == 0


def test_a_team_unlike_its_track_stops_the_export(game):
    report = {'events': [moment(312 + k, 3, team='team_b') for k in range(5)]}
    with pytest.raises(ValueError, match='disagrees with the stats'):
        vd.build('xy', game, report, 'lighter')


def test_possession_buckets(game):
    index, _ = vd.build('xy', game, {'events': []}, 'lighter')
    p = index['possession']
    assert p['bucket_s'] == vd.BUCKET_S
    assert p['halves'][0] == {'f0': 300, 'a': [60], 'b': [0]}


def test_a_half_without_holds_still_exports(game, tmp_path):
    game[1].holds_path = tmp_path / 'missing.npz'
    index, chunks = vd.build('xy', game, {'events': []}, 'lighter')
    assert [h['holds'] for h in index['halves']] == [True, False]
    assert set(arr(chunks[1], 'ht', np.uint8).tolist()) == {vd.NO_HOLD}
    assert index['possession']['halves'][1]['a'] == []


def test_long_halves_split_into_chunks(game, monkeypatch):
    monkeypatch.setattr(vd, 'CHUNK_S', 1)  # 5 samples a chunk
    index, chunks = vd.build('xy', game, {'events': []}, 'lighter')
    assert index['halves'][0]['chunks'] == ['xy1_00', 'xy1_01', 'xy1_02']
    assert [c['f0'] for c in chunks[:3]] == [300, 330, 360]
    assert sum(c['samples'] for c in chunks[:3]) == 15


def test_the_command_writes_index_and_chunks(game, tmp_path, monkeypatch):
    monkeypatch.setattr(viewer_export, 'inputs', lambda g, run_dir: game)
    rep = tmp_path / 'r.json'
    rep.write_text(json.dumps({'events': [moment(312, 3)]}))
    out = tmp_path / 'view'
    assert (
        viewer_export.main(['xy', '--ours', 'lighter', '--report', str(rep), '--out', str(out)])
        == 0
    )
    index = json.loads((out / 'index.json').read_text())
    assert len(index['moments']) == 1
    assert sorted(p.name for p in out.iterdir()) == ['index.json', 'xy1_00.json', 'xy2_00.json']
