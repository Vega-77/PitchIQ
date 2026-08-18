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
EVENTS_DOC = 'events'

# The cropped pictures, kept apart from everything else in cvStats — and this is
# a privacy decision rather than a data-modelling one.
#
# They are photographs of children, cut out of match footage. They exist for one
# job: letting a coach look at a tracked figure and say which of their players it
# is. Once that mapping is confirmed the job is done, and what is left is a set
# of pictures of minors with no remaining purpose, sitting in a database
# forever.
#
# While they lived inside `identity` alongside the clusters and the per-track
# stats, nobody could remove them without destroying every number in the same
# document — and `cvStats` is `allow write: if false` to every client, so in
# practice nobody could remove them at all. In their own document a coach can
# delete the pictures and keep the match. See `firestore.rules`, which grants
# delete on this one path and nothing else: a client may destroy these, and
# still may not fabricate a statistic.
THUMBS_DOC = 'thumbs'

# Individual events written to Firestore, for the coach review tool.
#
# Deliberately capped rather than paginated. A half produces a few hundred of
# these; a run that produces thousands has gone wrong, and the right response to
# that is a truncated document plus a flag saying so, not a document that fails
# to write at all because it crossed a megabyte.
MAX_EVENTS = 1500

# Why the pipeline left figures out, written alongside the stats they were left
# out of. Only the tracks something was decided about — the ones it kept and
# believed carry no news, and there are forty of those for every one of these.
#
# Capped because a school pitch with a crowd behind it produces a lot of
# stationary people. The authoritative counts are in `quality.excluded_tracks`
# and `quality.flagged_officials`, so truncating this list loses the reasons for
# the least significant few and never the size of the correction.
MAX_PARTICIPANT_NOTES = 40

# CV fields added to a playerReports document. All prefixed, so a coach's
# tagged figures and the pipeline's estimates can never be confused for one
# another, and so removing them later is a single filter.
CV_FIELD_PREFIX = 'cv'

# Touch timestamps written per player report. A full match is thousands, and
# both the document size limit and the readability of a timeline strip run out
# well before that.
MAX_TOUCH_TIMES = 400

# Kept in step with NOT_A_PLAYER in assets/report.js, which is where it is
# explained. Duplicated rather than shared because there is no build step and
# nothing carries a constant across the two languages.
NOT_A_PLAYER = '__not_a_player'

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

    `reconciliation` does travel, whole. It is a rate, three counts and the
    handful of goals the two records disagree about — a few hundred bytes, and
    the one part of the report that tells a reviewer where to start.

    The `timing` block does not travel, and `quality.realtime_factor` does. The
    split is deliberate: which stage to shorten is a question for whoever runs
    the pipeline, and whether the half-time report would have been late is a
    question for the coach waiting on it. Only the second one belongs in a
    document every client reads.
    """
    return {
        'schemaVersion': report_json.get('schema_version'),
        'source': report_json.get('source'),
        'window': report_json.get('window') or {},
        'durationS': report_json.get('duration_s'),
        'calibrated': report_json.get('calibrated', False),
        'period': report_json.get('period'),
        'periodSource': report_json.get('period_source'),
        'calibrationErrorM': report_json.get('calibration_error_m'),
        'quality': report_json.get('quality') or {},
        'warnings': report_json.get('warnings') or [],
        'trustworthy': report_json.get('trustworthy', False),
        'teams': report_json.get('teams') or {},
        'keepers': report_json.get('keepers') or [],
        'participants': participant_notes(report_json),
        'reconciliation': report_json.get('reconciliation'),
    }


def participant_notes(
    report_json: dict, limit: int = MAX_PARTICIPANT_NOTES,
) -> list[dict]:
    """The tracks the classifier acted on, and what it said about each.

    An exclusion a coach cannot see is indistinguishable from a bug, and one
    they can see but cannot question is worse — it looks like the pipeline knows
    something it does not. Every threshold in `cv/participants.py` is a guess,
    so the sentence that guess produced travels with the count all the way to
    the screen.

    Longest on screen first: a figure the pipeline watched for forty minutes and
    then dropped is a bigger claim than one it saw for twenty-five seconds.
    """
    # Named rather than "everything that is not a player". `unsure` is a real
    # role and it is *kept* — a track seen for fifteen seconds is treated as a
    # player, because the alternative is deleting someone for being brief. It is
    # not news, and listing it here would read as a third kind of rejection.
    acted_on = [
        p for p in report_json.get('participants') or []
        if p.get('role') in ('offfield', 'official')
    ]
    acted_on.sort(key=lambda p: p.get('screen_time_s') or 0.0, reverse=True)

    return [
        {
            'trackId': p.get('track_id'),
            'role': p.get('role'),
            'reason': p.get('reason'),
            'screenTimeS': p.get('screen_time_s'),
        }
        for p in acted_on[:limit]
    ]


def _round_pair(point) -> list[float] | None:
    """An (x, y) in metres to one decimal place, or None.

    A tenth of a metre is already far finer than the homography behind it — the
    trust ladder starts writing figures off at half a metre — so the extra
    digits are only bytes, and there are up to 1500 of these in one document.
    """
    if point is None:
        return None
    return [round(float(point[0]), 1), round(float(point[1]), 1)]


def events_payload(report_json: dict, limit: int = MAX_EVENTS) -> dict:
    """The individual events, for a human to check one at a time.

    `summary_payload` deliberately leaves these out, and that was right for what
    it is: nothing renders a team total from an event list. The review tool is
    the opposite job — it exists to put each candidate in front of a coach with
    the video beside it — and it has no other source for them.

    Trimmed to what that tool needs, plus `start_m` — where the ball was when
    the event happened. That was dropped originally because it is null without a
    calibration and nothing plotted a pitch; both halves of that reason have
    since stopped being true, and the passing network is built from these
    positions. `end_m` stays out: the network aggregates by player, so the
    receiver's id says everything the far end of the line would. `tags` stay out
    for the reason `start_m` used to.

    Roughly 140 bytes an event, so a full cap is about 210KB against
    Firestore's 1MB.
    """
    events = report_json.get('events') or []
    floor = None

    if len(events) > limit:
        # Keep the most confident, but hand them back in clock order: a
        # reviewer works down a timeline, not a ranking. And say what was
        # dropped — a list that silently stops is indistinguishable from a
        # pipeline that stopped finding things.
        ranked = sorted(events, key=lambda e: e.get('confidence') or 0.0, reverse=True)
        kept = ranked[:limit]
        floor = min((e.get('confidence') or 0.0) for e in kept)
        events = sorted(kept, key=lambda e: e.get('timestamp_s') or 0.0)

    counts: dict[str, int] = {}
    for event in events:
        kind = event.get('type') or 'unknown'
        counts[kind] = counts.get(kind, 0) + 1

    return {
        'schemaVersion': report_json.get('schema_version'),
        'events': [
            {
                'id': event.get('event_id'),
                'type': event.get('type'),
                'timestampS': event.get('timestamp_s'),
                'trackId': event.get('track_id'),
                'team': event.get('team'),
                'confidence': event.get('confidence'),
                'inPlay': event.get('in_play', True),
                'outcome': event.get('outcome'),
                'xg': event.get('xg'),
                # The same shot scored as a header, so the shot log can apply a
                # coach's body-part tag without a model in the browser.
                'xgHeader': event.get('xg_header'),
                'receiverTrackId': event.get('receiver_track_id'),
                # Where the ball was, in pitch metres, at the moment of the
                # event. Null on an uncalibrated run, which is the whole reason
                # every consumer of it has to handle absent rather than zero.
                'startM': _round_pair(event.get('start_m')),
            }
            for event in events
        ],
        'truncated': floor is not None,
        'droppedBelowConfidence': floor,
        'counts': counts,
    }


def identity_payload(report_json: dict, mapping: dict[str, str] | None = None) -> dict:
    """The clusters, and whichever of them a human has put a name to.

    Heatmaps are flattened on the way through. Firestore refuses nested arrays
    outright — a list of lists cannot be written at all — so passing `tracks`
    straight through would have failed the whole publish the first time a
    calibrated run produced one. It never has, which is the only reason this was
    not found sooner.

    The pictures come out here and go to `thumbs_payload` instead, so that
    deleting them cannot take a statistic with it. See `THUMBS_DOC`.
    """
    tracks = []
    for track in report_json.get('tracks') or []:
        if track.get('heatmap') is None:
            tracks.append(track)
            continue
        tracks.append(track | {'heatmap': _flat_heatmap(track['heatmap'])})

    clusters = [
        {k: v for k, v in cluster.items() if k not in THUMB_FIELDS}
        for cluster in (report_json.get('clusters') or [])
    ]

    return {
        'clusters': clusters,
        'tracks': tracks,
        # cluster_id -> playerId. Empty until a coach fills it in, which is the
        # gate on anything per-player reaching a season.
        'playerByCluster': mapping or {},
    }


# What `cv/identity.py` puts on a cluster to describe its picture. Named once so
# that stripping them out and writing them back cannot drift apart.
THUMB_FIELDS = ('thumb', 'thumb_height_px')


def thumbs_payload(report_json: dict) -> dict:
    """The pictures, keyed by cluster, in a document of their own.

    Keyed rather than a list so a reader can join without caring about order,
    and so a cluster with no clear view of it is simply absent — which the
    picker already draws as "no clear view", the same thing it draws once a
    coach has deleted the lot. That the two look identical is deliberate: a
    figure whose picture was never captured and one whose picture has been
    removed are both figures you cannot see, and neither is a fault.
    """
    by_cluster = {}
    for cluster in report_json.get('clusters') or []:
        if not cluster.get('thumb'):
            continue
        by_cluster[str(cluster['cluster_id'])] = {
            'thumb': cluster['thumb'],
            'thumb_height_px': cluster.get('thumb_height_px'),
        }
    return {'byCluster': by_cluster}


def player_report_fields(
    track_stats: dict,
    attacking_end: str | None = None,
    calibration_error_m: float | None = None,
) -> dict:
    """CV fields for one player's match report, all prefixed.

    Prefixed so a coach looking at a report can always tell which numbers a
    human tapped and which the pipeline estimated. Nothing here overwrites a
    tagged field, and no tagged field is read.
    """
    return {
        f'{CV_FIELD_PREFIX}Touches': track_stats.get('touches'),
        f'{CV_FIELD_PREFIX}PassesAttempted': track_stats.get('passes_attempted'),
        f'{CV_FIELD_PREFIX}PassesCompleted': track_stats.get('passes_completed'),
        # No `cvPassAccuracy`. It was written here until 2026-08-17 and read by
        # nothing: every page divides the two fields above instead. That made it
        # the one field this side writes and `cvReportFields` does not clear, so
        # a coach who un-mapped a player and re-published left a pass accuracy
        # standing beside the two nulls it was supposedly derived from — a trap
        # for whoever reads it next, in exchange for a number already on the
        # document twice over.
        f'{CV_FIELD_PREFIX}Carries': track_stats.get('carries'),
        f'{CV_FIELD_PREFIX}Tackles': track_stats.get('tackles'),
        f'{CV_FIELD_PREFIX}Interceptions': track_stats.get('interceptions'),
        f'{CV_FIELD_PREFIX}Recoveries': track_stats.get('recoveries'),
        f'{CV_FIELD_PREFIX}Shots': track_stats.get('shots'),
        f'{CV_FIELD_PREFIX}Xg': track_stats.get('xg'),
        f'{CV_FIELD_PREFIX}DistanceM': track_stats.get('distance_m'),
        f'{CV_FIELD_PREFIX}TopSpeedKmh': track_stats.get('top_speed_kmh'),
        f'{CV_FIELD_PREFIX}SprintCount': track_stats.get('sprint_count'),
        # Bursts, and the wobble that decides whether they were worth counting.
        # Both may be null, and null is not zero here: a fragment shorter than a
        # burst window, or a track too noisy to read one off, is a question that
        # went unanswered rather than a player who never accelerated.
        f'{CV_FIELD_PREFIX}Accelerations': track_stats.get('accelerations'),
        f'{CV_FIELD_PREFIX}PositionNoiseM': track_stats.get('position_noise_m'),
        f'{CV_FIELD_PREFIX}MinutesTracked': track_stats.get('minutes_tracked'),
        # Every touch, in seconds, so the player portal can mark them on the
        # match video. Capped because a full match of touches is thousands of
        # numbers and a Firestore document stops at a megabyte — and a strip
        # with two thousand ticks on it is unreadable anyway.
        f'{CV_FIELD_PREFIX}TouchTimes': (track_stats.get('touch_times_s') or [])[:MAX_TOUCH_TIMES],
        # Where they spent the match, as a 12x8 occupancy grid. Stored as a flat
        # list with its shape beside it, because Firestore refuses nested arrays
        # — a list of lists cannot be written at all, which is a rule that
        # produces a runtime error rather than a lint failure.
        #
        # ~96 floats, so it costs less than the touch times next to it.
        f'{CV_FIELD_PREFIX}Heatmap': _flat_heatmap(track_stats.get('heatmap')),
        # Which way they were playing, so the heatmap above can be read.
        f'{CV_FIELD_PREFIX}AttackingEnd': attacking_end,
        # Their own shots as points, already mirrored to attack right. A dozen
        # a half at most, and each one is a flat dict of numbers — no nested
        # arrays, which Firestore would refuse.
        f'{CV_FIELD_PREFIX}ShotMap': track_stats.get('shot_map'),
        # How good the homography was, carried per report because a player never
        # reads the team document. Without it the player portal would have no
        # way to apply `xgTrust` and would size its shot map by an xG the
        # coach's own page had already decided was too loose to size by — the
        # same match, told two different ways, on the page with less context to
        # spot it.
        f'{CV_FIELD_PREFIX}CalibrationErrorM': calibration_error_m,
    }


def _flat_heatmap(grid) -> dict | None:
    """A 2-D grid as `{cols, rows, values}`, or None if there is nothing.

    None rather than an empty grid: a player with no heatmap was not tracked in
    metres, and a grid of zeroes would draw as a pitch somebody stood still on.
    """
    if not grid:
        return None
    rows = len(grid[0]) if grid[0] else 0
    if not rows:
        return None
    return {
        'cols': len(grid),
        'rows': rows,
        'values': [round(float(v), 5) for column in grid for v in column],
    }


# How each figure's numbers combine when a coach names two of them as the same
# person. Kept identical to `SUMMED` and `MAXED` in assets/report.js — the two
# sides publish the same player's match to two different screens, and a stat
# that adds on one and replaces on the other is one match told two ways.
SUMMED_FIELDS = (
    'touches', 'passes_attempted', 'passes_completed', 'carries',
    'tackles', 'interceptions', 'recoveries', 'shots', 'goals', 'xg',
    'distance_m', 'sprint_count', 'sprint_distance_m', 'minutes_tracked',
    'accelerations',
)
# Taken at the worst fragment rather than averaged. A player assembled from a
# clean track and a jittery one is only as trustworthy as the jittery one, and
# an average would hide that behind the clean half.
MAXED_FIELDS = ('top_speed_kmh', 'position_noise_m')


def merge_tracks(tracks: list[dict]) -> dict:
    """Several tracked fragments of one person, added up as one person.

    A player is legitimately several clusters. The tracker loses people when
    they leave frame and `cv/identity.py` only rejoins fragments seconds apart,
    so anyone who went off and came back stays split — and the picker lets a
    coach say so on purpose, because a wrong automatic merge would credit one
    student with another's work and could not be undone.

    Until 2026-08-17 this function did not exist and the loop below wrote one
    document per *cluster*. Two fragments of the same player meant two writes to
    the same report, so the second silently replaced the first and the student
    was shown whichever fragment happened to come last in the mapping: 24
    touches of 54, 3.1 km of 7.3. The browser has always summed them, and says
    so in `cvStatsByPlayer`'s docstring — so the coach's screen and the
    student's own report disagreed about the same afternoon, with the student's
    the smaller of the two.

    None is not zero anywhere here. A fragment too short to answer a question
    contributes nothing to it rather than pulling the total towards none, and a
    player whose fragments all declined to answer ends with no figure at all.
    """
    if len(tracks) == 1:
        return tracks[0]

    # Longest first, so the fields that cannot be combined — which team this
    # figure was on — come from the fragment with the most evidence behind them.
    ordered = sorted(tracks, key=lambda t: t.get('minutes_tracked') or 0, reverse=True)
    merged = dict(ordered[0])

    for field in SUMMED_FIELDS:
        values = [t.get(field) for t in ordered if t.get(field) is not None]
        merged[field] = sum(values) if values else None
    for field in MAXED_FIELDS:
        values = [t.get(field) for t in ordered if t.get(field) is not None]
        merged[field] = max(values) if values else None

    # Recomputed, never averaged: the accuracy of two fragments is the accuracy
    # of everything they attempted between them.
    attempted = merged.get('passes_attempted')
    merged['pass_accuracy'] = (
        (merged.get('passes_completed') or 0) / attempted if attempted else None
    )

    touches: list[float] = []
    shots: list[dict] = []
    for track in ordered:
        touches.extend(track.get('touch_times_s') or [])
        shots.extend(track.get('shot_map') or [])
    # Sorted, because the fragments arrive in mapping order and a touch strip in
    # mapping order is not a timeline.
    merged['touch_times_s'] = sorted(touches)
    merged['shot_map'] = sorted(shots, key=lambda s: s.get('video_s') or 0)

    merged['heatmap'] = _merge_heatmaps([t.get('heatmap') for t in ordered])
    merged['cluster_ids'] = sorted(t.get('cluster_id') for t in ordered)
    return merged


def _merge_heatmaps(grids) -> list[list[float]] | None:
    """Occupancy grids added cell by cell, or None if nobody had one.

    Cell-wise because a heatmap counts time spent, and where a player stood
    before they left the frame is as much part of their match as where they
    stood after. Grids of different shapes are a bug rather than a case to
    handle: every one comes from the same `Pitch`, so a mismatch means two runs
    got mixed and the honest answer is no heatmap at all.
    """
    present = [g for g in grids if g]
    if not present:
        return None
    shape = (len(present[0]), len(present[0][0]) if present[0][0] else 0)
    if any((len(g), len(g[0]) if g[0] else 0) != shape for g in present):
        return None
    return [
        [sum(g[col][row] for g in present) for row in range(shape[1])]
        for col in range(shape[0])
    ]


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

    written = {
        'summary': True, 'identity': True, 'thumbs': 0, 'events': 0,
        'playerReports': 0, 'skipped': [],
    }

    events = events_payload(report_json)
    written['events'] = len(events['events'])

    thumbs = thumbs_payload(report_json)
    written['thumbs'] = len(thumbs['byCluster'])

    stats.document(SUMMARY_DOC).set(summary_payload(report_json))
    stats.document(IDENTITY_DOC).set(identity_payload(report_json, mapping))
    stats.document(EVENTS_DOC).set(events)
    # Written with `set`, so re-publishing a match restores pictures a coach
    # deleted. That is the right way round: the deletion is about not keeping
    # them lying about, and someone who re-runs the pipeline has asked for them
    # back. It is also why the control says the pipeline can produce them again.
    stats.document(THUMBS_DOC).set(thumbs)

    if not mapping:
        written['skipped'].append(
            'per-player stats: no cluster-to-player mapping confirmed yet'
        )
        return written

    by_cluster = {
        str(track['cluster_id']): track for track in report_json.get('tracks') or []
    }

    # Grouped by player before anything is written, so a student named as two
    # tracked figures gets one document holding both of them rather than two
    # writes where the last one wins. See `merge_tracks`.
    tracks_for: dict[str, list[dict]] = {}
    for cluster_id, player_id in mapping.items():
        # The coach's way of saying a tracked figure is nobody — a referee, or
        # somebody on the bench. It shares the map with real player ids because
        # the cvMapping rules pin that document to three keys; see NOT_A_PLAYER
        # in assets/report.js. Both readers of the map have to skip it, and this
        # is the second.
        if player_id == NOT_A_PLAYER:
            continue

        track = by_cluster.get(str(cluster_id))
        if track is None:
            written['skipped'].append(f'cluster {cluster_id}: no stats for it')
            continue
        tracks_for.setdefault(player_id, []).append(track)

    for player_id, player_tracks in tracks_for.items():
        track = merge_tracks(player_tracks)

        report_ref = match_ref.collection('playerReports').document(player_id)
        # Update, never create: a report exists because a coach published the
        # match. A missing one is a mismatch worth seeing, not something to
        # invent a document for.
        if not report_ref.get().exists:
            written['skipped'].append(
                f'player {player_id}: no published report to add CV stats to'
            )
            continue

        # The team's attacking end travels with the player, since it is what
        # makes their heatmap readable and they never see the team document.
        end = ((report_json.get('teams') or {}).get(track.get('team')) or {}
               ).get('attacking_end')
        report_ref.update(player_report_fields(
            track,
            attacking_end=end,
            calibration_error_m=report_json.get('calibration_error_m'),
        ))
        written['playerReports'] += 1

    return written
