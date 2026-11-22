"""The match viewer's documents on their way to Firestore (session step 3.9).

`publish_viewer` writes what `viewer_export` packed to `cvViewer`: the chunks, the moments,
and the index last, so a page that finds the index finds every chunk it names. These check
that split, the refusals (a chunk the index doesn't name, a document over Firestore's 1 MB,
an id that would leave the collection), that a re-run's dropped chunks are removed, and
that the run carries on when the viewer fails.

Run:  .venv/Scripts/python.exe -m pytest tests/test_publish_viewer.py -q
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pitchiq.cli import process_match as pm
from pitchiq.cli import publish_viewer as cli
from pitchiq.publish_to_firestore import (
    MAX_VIEWER_DOC_BYTES,
    PublishError,
    publish_viewer,
    viewer_docs,
)


class FakeDoc:
    def __init__(self, client, path):
        self.client = client
        self.path = path

    @property
    def id(self):
        return self.path.rsplit('/', 1)[-1]

    def set(self, data):
        self.client.log.append(('set', self.path))
        self.client.store[self.path] = dict(data)

    def delete(self):
        self.client.log.append(('delete', self.path))
        self.client.store.pop(self.path, None)

    def collection(self, name):
        return FakeCollection(self.client, f'{self.path}/{name}')


class FakeCollection:
    def __init__(self, client, path):
        self.client = client
        self.path = path

    def document(self, name):
        return FakeDoc(self.client, f'{self.path}/{name}')

    def list_documents(self):
        inside = [p for p in self.client.store if p.rsplit('/', 1)[0] == self.path]
        return [FakeDoc(self.client, p) for p in inside]


class FakeClient:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.log: list[tuple[str, str]] = []

    def collection(self, name):
        return FakeCollection(self, name)


BASE = 'teams/T/matches/M/cvViewer'


def an_export(chunk_ids=('xy1_00', 'xy1_01', 'xy2_00'), moments=3):
    halves = {}
    for cid in chunk_ids:
        halves.setdefault(cid[:3], []).append(cid)
    index = {
        'schema': 1,
        'game': 'xy',
        'halves': [{'tag': t, 'chunks': ids} for t, ids in halves.items()],
        'moments': [{'t': float(i), 'type': 'pass'} for i in range(moments)],
    }
    chunks = [{'id': cid, 'samples': 2, 'n': 'AgI='} for cid in chunk_ids]
    return index, chunks


def test_the_index_loses_its_moments_and_goes_last():
    index, chunks = an_export()
    docs = viewer_docs(index, chunks)
    assert list(docs) == ['xy1_00', 'xy1_01', 'xy2_00', 'moments', 'index']
    assert 'moments' not in docs['index']
    assert docs['index']['counts'] == {'chunks': 3, 'moments': 3}
    assert len(docs['moments']['moments']) == 3
    assert 'moments' in index  # the caller's index is left as it was


def test_chunks_the_index_does_not_name_are_refused():
    index, chunks = an_export()
    with pytest.raises(PublishError, match='names 3 chunks and 2 were given'):
        viewer_docs(index, chunks[:2])
    with pytest.raises(PublishError, match='appears twice'):
        viewer_docs(index, chunks + chunks[:1])


@pytest.mark.parametrize('bad', ['', 'a/b', 'index', 'moments', '..'])
def test_a_chunk_id_must_be_a_plain_document_id(bad):
    index, chunks = an_export(chunk_ids=('xy1_00',))
    chunks[0]['id'] = bad
    index['halves'][0]['chunks'] = [bad]
    with pytest.raises(PublishError, match='not a valid document id'):
        viewer_docs(index, chunks)


def test_a_document_over_a_megabyte_is_refused_before_anything_is_written():
    index, chunks = an_export()
    chunks[1]['x'] = 'A' * MAX_VIEWER_DOC_BYTES
    client = FakeClient()
    with pytest.raises(PublishError, match='xy1_01 is 1.00 MB'):
        publish_viewer(index, chunks, 'T', 'M', client=client)
    assert client.log == []


def test_a_rerun_removes_the_chunks_it_no_longer_has():
    client = FakeClient()
    publish_viewer(*an_export(), 'T', 'M', client=client)
    assert sorted(client.store) == sorted(
        f'{BASE}/{d}' for d in ('xy1_00', 'xy1_01', 'xy2_00', 'moments', 'index')
    )
    client.log.clear()
    written = publish_viewer(*an_export(chunk_ids=('xy1_00', 'xy2_00')), 'T', 'M', client=client)
    assert written['removed'] == ['xy1_01']
    assert f'{BASE}/xy1_01' not in client.store
    # Deleted only after the new index is in place.
    assert client.log.index(('delete', f'{BASE}/xy1_01')) > client.log.index(
        ('set', f'{BASE}/index')
    )


def test_a_bad_match_id_is_refused():
    with pytest.raises(PublishError):
        publish_viewer(*an_export(), 'T', 'a/b', client=FakeClient())


def write_export(folder, index, chunks):
    folder.mkdir()
    (folder / 'index.json').write_text(json.dumps(index))
    for c in chunks:
        (folder / f'{c["id"]}.json').write_text(json.dumps(c))


def test_the_command_dry_runs_an_export_folder(tmp_path, capsys):
    write_export(tmp_path / 'view', *an_export())
    assert cli.main([str(tmp_path / 'view'), '--team', 'T', '--match', 'M', '--dry-run']) == 0
    out = capsys.readouterr().out
    assert 'would publish to teams/T/matches/M/cvViewer' in out
    assert '5 documents: 3 chunks, 3 moments' in out


def test_the_command_refuses_a_folder_missing_a_chunk(tmp_path, capsys):
    index, chunks = an_export()
    write_export(tmp_path / 'view', index, chunks[:2])
    assert cli.main([str(tmp_path / 'view'), '--team', 'T', '--match', 'M', '--dry-run']) == 2
    assert 'xy2_00' in capsys.readouterr().err


def a_run(tmp_path, rcs):
    said, told, ran = [], [], []

    def py(name, *args, check=True):
        ran.append((name, check))
        return rcs.get(name, 0)

    r = SimpleNamespace(
        g='xy', out=tmp_path, logs=tmp_path / 'logs', py=py, say=said.append,
        tell=lambda *x: told.append(x), a=SimpleNamespace(ours='lighter', team='T', match='M'),
    )  # fmt: skip
    return r, said, told, ran


def test_a_viewer_that_fails_does_not_stop_the_run(tmp_path):
    r, said, told, ran = a_run(tmp_path, {'viewer': 1})
    pm.publish_viewer(r, tmp_path / 'xy_report.json')
    assert ran == [('viewer', False)]  # not tried twice, and not published
    assert told and told[0][0] == 'no match viewer'
    assert 'stats are published without the match viewer' in said[-1]


def test_a_viewer_that_exports_is_published(tmp_path):
    r, said, told, ran = a_run(tmp_path, {})
    pm.publish_viewer(r, tmp_path / 'xy_report.json')
    assert ran == [('viewer', False), ('publish_viewer', False)]
    assert not told
    assert said[-1] == 'match viewer published to match M'
