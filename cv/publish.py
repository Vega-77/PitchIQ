"""Getting CV stats out of Python and into the app.

This module holds a credential that **bypasses every Firestore security rule**.
The rules are the entire security boundary of this app, and the app stores the
names and email addresses of minors. So the interesting part of this file is
not the writing; it is the four things it refuses to do.

**The key never lives in the repo.** Its path comes from `PITCHIQ_SA_KEY` and
nowhere else, and `_check_key` refuses to run if that path resolves anywhere
inside the working tree. A service-account JSON committed to a public GitHub
Pages repo is a total compromise of every team's data, and it is an easy
mistake — the file looks like config.

**It writes to an allowlist of paths, not wherever it is told.** Everything
here goes under `teams/{t}/matches/{m}/cvStats/*` or adds `cv*` fields to an
existing player report. It never touches `players`, `users`, `invites` or
`teams` — the documents holding personal data — so a bug in this file cannot
leak or destroy any of it.

**It never creates player reports, only updates them.** A report exists because
a coach published a match. If one is missing, that is a mismatch worth
surfacing rather than papering over with a document nobody asked for.

**It refuses to write per-player stats from unconfirmed clusters.** At the
fragmentation this pipeline measures, a cluster is a guess about identity until
a human agrees with it. Writing guesses into a named player's season is exactly
the thing that would make the whole feature untrustworthy.

Setup, once:

    setx PITCHIQ_SA_KEY "C:\\Users\\you\\keys\\pitchiq.sa.json"

The key is issued from the Firebase console under Project settings → Service
accounts → Generate new private key. Keep it outside the repo. `*.sa.json` is
gitignored as a second line of defence, but the check below is the real one.
"""

from __future__ import annotations

import os
from pathlib import Path

# Everything this module is allowed to write, as a path shape. Anything else is
# a bug, and `_check_path` turns it into an exception rather than a silent
# write to somewhere it should not reach.
CV_STATS_COLLECTION = 'cvStats'
SUMMARY_DOC = 'summary'
IDENTITY_DOC = 'identity'

# CV fields added to a playerReports document. All prefixed, so a coach's
# tagged figures and the pipeline's estimates can never be confused for one
# another, and so removing them later is a single filter.
CV_FIELD_PREFIX = 'cv'

# Touch timestamps written per player report. A full match is thousands, and
# both the document size limit and the readability of a timeline strip run out
# well before that.
MAX_TOUCH_TIMES = 400

REPO_ROOT = Path(__file__).resolve().parents[1]


class PublishError(RuntimeError):
    pass


def _check_key(path: str | None) -> Path:
    """Where the service-account key is, or a refusal explaining why not."""
    if not path:
        raise PublishError(
            'PITCHIQ_SA_KEY is not set. It must point to a Firebase service '
            'account JSON kept OUTSIDE this repository.'
        )

    key = Path(path).expanduser().resolve()
    if not key.is_file():
        raise PublishError(f'no service account key at {key}')

    # The check that matters. A service account key committed to the repo that
    # publishes this site is a complete compromise of every team's data, and
    # nothing downstream would notice.
    try:
        key.relative_to(REPO_ROOT)
    except ValueError:
        return key
    raise PublishError(
        f'the service account key at {key} is inside the repository. Move it '
        'somewhere outside the working tree — a key committed here would give '
        'anyone who reads the repo full access to every team, including '
        "players' names and email addresses."
    )


def _client(key_path: str | None = None):
    """A Firestore client from the service account, imported lazily.

    Lazily because `firebase-admin` is a publishing dependency, and importing
    `cv.publish` for its constants should not require it to be installed.
    """
    key = _check_key(key_path or os.environ.get('PITCHIQ_SA_KEY'))

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError as exc:                       # pragma: no cover
        raise PublishError(
            'firebase-admin is not installed. pip install firebase-admin'
        ) from exc

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(key)))
    return firestore.client()


def _check_path(team_id: str, match_id: str) -> None:
    """Reject anything that is not a plain document id.

    A slash here would silently retarget the write at a different collection,
    which is how an allowlist stops being one.
    """
    for name, value in (('team', team_id), ('match', match_id)):
        if not value or '/' in value or value in ('.', '..'):
            raise PublishError(f'{name} id is not a valid document id: {value!r}')


def summary_payload(report_json: dict) -> dict:
    """The team-level document. Deliberately not the whole report.

    The per-event list and per-touch list are left out: a half of football is
    tens of thousands of touches, Firestore documents cap at a megabyte, and
    nothing in the app reads individual events. They stay in the JSON file for
    anyone doing analysis.
    """
    return {
        'schemaVersion': report_json.get('schema_version'),
        'source': report_json.get('source'),
        'window': report_json.get('window') or {},
        'durationS': report_json.get('duration_s'),
        'calibrated': report_json.get('calibrated', False),
        'calibrationErrorM': report_json.get('calibration_error_m'),
        'quality': report_json.get('quality') or {},
        'warnings': report_json.get('warnings') or [],
        'trustworthy': report_json.get('trustworthy', False),
        'teams': report_json.get('teams') or {},
        'keepers': report_json.get('keepers') or [],
    }


def identity_payload(report_json: dict, mapping: dict[str, str] | None = None) -> dict:
    """The clusters, and whichever of them a human has put a name to."""
    return {
        'clusters': report_json.get('clusters') or [],
        'tracks': report_json.get('tracks') or [],
        # cluster_id -> playerId. Empty until a coach fills it in, which is the
        # gate on anything per-player reaching a season.
        'playerByCluster': mapping or {},
    }


def player_report_fields(track_stats: dict) -> dict:
    """CV fields for one player's match report, all prefixed.

    Prefixed so a coach looking at a report can always tell which numbers a
    human tapped and which the pipeline estimated. Nothing here overwrites a
    tagged field, and no tagged field is read.
    """
    return {
        f'{CV_FIELD_PREFIX}Touches': track_stats.get('touches'),
        f'{CV_FIELD_PREFIX}PassesAttempted': track_stats.get('passes_attempted'),
        f'{CV_FIELD_PREFIX}PassesCompleted': track_stats.get('passes_completed'),
        f'{CV_FIELD_PREFIX}PassAccuracy': track_stats.get('pass_accuracy'),
        f'{CV_FIELD_PREFIX}Carries': track_stats.get('carries'),
        f'{CV_FIELD_PREFIX}Tackles': track_stats.get('tackles'),
        f'{CV_FIELD_PREFIX}Interceptions': track_stats.get('interceptions'),
        f'{CV_FIELD_PREFIX}Recoveries': track_stats.get('recoveries'),
        f'{CV_FIELD_PREFIX}Shots': track_stats.get('shots'),
        f'{CV_FIELD_PREFIX}Xg': track_stats.get('xg'),
        f'{CV_FIELD_PREFIX}DistanceM': track_stats.get('distance_m'),
        f'{CV_FIELD_PREFIX}TopSpeedKmh': track_stats.get('top_speed_kmh'),
        f'{CV_FIELD_PREFIX}SprintCount': track_stats.get('sprint_count'),
        f'{CV_FIELD_PREFIX}MinutesTracked': track_stats.get('minutes_tracked'),
        # Every touch, in seconds, so the player portal can mark them on the
        # match video. Capped because a full match of touches is thousands of
        # numbers and a Firestore document stops at a megabyte — and a strip
        # with two thousand ticks on it is unreadable anyway.
        f'{CV_FIELD_PREFIX}TouchTimes': (track_stats.get('touch_times_s') or [])[:MAX_TOUCH_TIMES],
    }


def publish(
    report_json: dict,
    team_id: str,
    match_id: str,
    mapping: dict[str, str] | None = None,
    key_path: str | None = None,
    client=None,
) -> dict:
    """Write a report to Firestore. Returns what was written, for the caller to print.

    `mapping` is cluster_id -> playerId, and comes from a coach confirming the
    clusters. Without it the team-level stats still publish and the per-player
    ones do not — which is the right default, because an unconfirmed cluster is
    a guess about who somebody is.
    """
    _check_path(team_id, match_id)
    db = client or _client(key_path)

    match_ref = (
        db.collection('teams').document(team_id)
        .collection('matches').document(match_id)
    )
    stats = match_ref.collection(CV_STATS_COLLECTION)

    written = {'summary': True, 'identity': True, 'playerReports': 0, 'skipped': []}

    stats.document(SUMMARY_DOC).set(summary_payload(report_json))
    stats.document(IDENTITY_DOC).set(identity_payload(report_json, mapping))

    if not mapping:
        written['skipped'].append(
            'per-player stats: no cluster-to-player mapping confirmed yet'
        )
        return written

    by_cluster = {
        str(track['cluster_id']): track for track in report_json.get('tracks') or []
    }

    for cluster_id, player_id in mapping.items():
        track = by_cluster.get(str(cluster_id))
        if track is None:
            written['skipped'].append(f'cluster {cluster_id}: no stats for it')
            continue

        report_ref = match_ref.collection('playerReports').document(player_id)
        # Update, never create: a report exists because a coach published the
        # match. A missing one is a mismatch worth seeing, not something to
        # invent a document for.
        if not report_ref.get().exists:
            written['skipped'].append(
                f'player {player_id}: no published report to add CV stats to'
            )
            continue

        report_ref.update(player_report_fields(track))
        written['playerReports'] += 1

    return written
