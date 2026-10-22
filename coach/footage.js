// The match video beside a match report, the tagged timeline it lights up,
// and attaching the footage in the first place.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { updateMatch, pushVideoToReports } from '../shared/db.js?v=151';
import { nowIndex } from '../shared/timeline.js?v=151';
import { renderMatchVideo, teamMarks } from '../shared/match-video.js?v=151';
import { clockMapNote, HALF_TIME, SECOND_HALF } from '../shared/report.js?v=151';
import { describeEvent, timelineTone } from '../shared/events.js?v=151';
import { videoKind } from '../shared/video.js?v=151';
import { byId, toast, clockText, plural, timelineRow } from '../shared/ui.js?v=151';
import { state, teamLabels } from './shell.js?v=151';
import { clockAt, clockMap, renderReview } from './review.js?v=151';

// ------------------------------------------------------------- the match video
//
// The same strip the player portal puts a teenager's own touches on, with the
// whole team's moments instead. Goals, cards and substitutions only — see
// teamMarks() for why the restarts and the fouls are left off.
//
// Hidden without a playable link, unlike the portal's version, which shows the
// moment list regardless. Here the moments are already listed in full further
// down the page, so a video block with no video would be the same information
// twice with the interesting half missing.

let matchVideo = null;

/**
 * Jump the match video to a match-clock moment.
 *
 * Separate from the review block's `reviewSeek`, which drives its own player.
 * Two videos on one page is unusual, but the review tool is a working surface
 * and the match video is the one a coach shows somebody — merging them would
 * mean scrolling away from the shot map to see the shot.
 */
export function seekMatchVideo(clockS) {
    if (!matchVideo) {
        toast('Add a playable video link to jump to a moment.', true);
        return;
    }
    matchVideo.seek(clockMap().toVideo(clockS));
    byId('match-video').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export function renderMatchVideoBlock() {
    const block = byId('match-video-block');
    const url = state.match?.videoUrl;

    matchVideo?.destroy?.();
    matchVideo = null;
    byId('match-now').innerHTML = '';

    if (!url) {
        block.classList.add('hidden');
        return;
    }
    block.classList.remove('hidden');

    const { usName, themName } = teamLabels();
    const nameById = new Map((state.match.roster || []).map((r) => [r.id, r.playerName]));
    const marks = teamMarks(state.match.log || [], (entry) => describeEvent(entry, {
        usName, themName, playerName: nameById.get(entry.playerId),
    }));

    matchVideo = renderMatchVideo(
        {
            video: byId('match-video'),
            strip: byId('match-scrubber'),
            list: byId('match-moments'),
            note: byId('match-video-note'),
        },
        {
            url,
            clock: clockMap(),
            marks,
            clockText,
            // So a mark tagged in stoppage time does not fall off the end of a
            // bar drawn to ninety minutes.
            extraTimes: (state.match.log || []).map((e) => e.matchClockS || 0),
            emptyText: 'No goals, cards or substitutions were tagged.',
            onClock: ({ videoS, clockS, period }) => {
                renderMatchNow(videoS, period);
                setTimelineNow(clockS);
            },
            notes: {
                embed: `${plural(marks.length, 'moment')} marked. Tap one to jump `
                    + 'straight to it, and the bar follows the video as it plays. '
                    + 'Only goals, cards and substitutions are marked — the '
                    + 'restarts would bury them.',
                link: 'That link cannot be played inside PitchIQ, so the times '
                    + 'below are match-clock readings rather than buttons.',
                none: '',
            },
        },
    );
}

/**
 * The match clock under the strip, moving with the video.
 *
 * The half is named rather than assumed. It is the one thing a reading alone
 * cannot say — 3:20 belongs to both halves — and it is also the visible proof
 * that the second-half anchor above is set, since without it `periodAt` has no
 * opinion and this says nothing about which half it is.
 */
function renderMatchNow(videoS, period) {
    const host = byId('match-now');
    host.innerHTML = '';

    const clock = document.createElement('span');
    clock.className = 'now-clock';
    clock.textContent = clockAt(videoS);

    const what = document.createElement('span');
    what.textContent = period === HALF_TIME
        ? 'the video is inside the interval'
        : (period
            ? `on the match clock — ${period === SECOND_HALF ? 'second' : 'first'} half`
            : 'on the match clock');

    host.append(clock, what);
}

/**
 * Say what we made of the link, as it is typed.
 *
 * The rule this exists for is invisible otherwise: a Google Drive share link is
 * a perfectly good link to footage and PitchIQ will not embed it, because the
 * page behind it is a Drive page rather than a video and putting an arbitrary
 * page in an iframe is not worth the convenience. A coach who pastes one and
 * gets no feedback until they open a player's report has been let down by the
 * form, not by the rule.
 */
export function updateVideoHint() {
    const hint = byId('video-kind-hint');
    const url = byId('input-video-url').value.trim();
    const kind = url ? videoKind(url) : null;

    hint.classList.remove('is-good', 'is-warn');
    if (!url) {
        hint.textContent = '';
        return;
    }

    if (kind === 'youtube') {
        hint.classList.add('is-good');
        hint.textContent = 'YouTube — plays here, and every moment becomes a button.';
    } else if (kind === 'file') {
        hint.classList.add('is-good');
        hint.textContent = 'A video file — plays here, and every moment becomes a button.';
    } else {
        hint.classList.add('is-warn');
        hint.textContent = 'This will be saved as a link players can open, but it '
            + 'will not play inside PitchIQ, so nothing will be tappable. A '
            + 'YouTube link, or a direct link to an .mp4, does both. A Google '
            + 'Drive or Hudl share page cannot be embedded.';
    }
}

/**
 * Draw what the two timing fields describe, and say what follows from it.
 *
 * The strip is the part worth having. Two numbers in two boxes are two numbers;
 * the same two drawn to scale are a recording with a lead-in, a half, a break
 * and a second half still running — and a break drawn as three quarters of the
 * bar is a typo a coach spots without reading anything.
 *
 * Only the part that is actually known gets drawn. The second half runs to the
 * end of the footage and nothing here knows where that is, so it is an open
 * segment rather than a segment sized by a guess at how long a half lasts.
 */
export function renderClockMap() {
    const host = byId('clock-map');
    const note = byId('clock-map-note');
    if (!host || !note) return;

    const inputs = {
        videoOffsetS: Number(byId('input-video-offset').value) || 0,
        secondHalfVideoS: secondHalfInput(),
        halfTimeClockS: state.match?.halfTimeClockS ?? null,
    };

    const { tone, text } = clockMapNote(inputs, clockText);
    note.textContent = text;
    note.classList.toggle('is-warn', tone === 'warn');
    note.classList.toggle('is-good', tone === 'ok');

    host.innerHTML = '';
    host.classList.toggle('hidden', tone !== 'ok');
    if (tone !== 'ok') return;

    const leadInS = Math.max(0, inputs.videoOffsetS);
    const firstHalfS = inputs.halfTimeClockS;
    const breakS = inputs.secondHalfVideoS - leadInS - firstHalfS;

    const segments = [
        ['lead-in', 'Before kick-off', leadInS],
        ['first', 'First half', firstHalfS],
        ['break', 'Half-time', breakS],
    ].filter(([, , seconds]) => seconds > 0);

    const total = segments.reduce((sum, [, , seconds]) => sum + seconds, 0);
    for (const [kind, label, seconds] of segments) {
        const part = document.createElement('span');
        part.className = `cm-part is-${kind}`;
        // Sized by share of the footage up to the restart, so the segments are
        // in proportion to each other and to nothing invented.
        part.style.flex = `${seconds / total}`;
        part.title = `${label} — ${clockText(seconds)}`;
        part.append(labelled('cm-label', label), labelled('cm-time', clockText(seconds)));
        host.append(part);
    }

    const rest = document.createElement('span');
    rest.className = 'cm-part is-second';
    rest.title = 'Second half — runs to the end of the footage';
    rest.append(labelled('cm-label', 'Second half'), labelled('cm-time', 'to the end'));
    host.append(rest);
}

function labelled(className, text) {
    const span = document.createElement('span');
    span.className = className;
    span.textContent = text;
    return span;
}

/** The typed second-half kick-off, or null for a field left blank. */
function secondHalfInput() {
    const raw = byId('input-second-half').value.trim();
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 ? value : null;
}

/** Tear the match video down. Called when the match view is left. */
export function leaveMatchVideo() {
    matchVideo?.destroy?.();
    matchVideo = null;
}

// The rows of the timeline in the order they were rendered, so the one the
// video is inside can be picked out. Newest first, which `nowIndex` does not
// care about — it is given the same array it is asked about.
let timelineRows = [];

/**
 * Light up the tagged entry the match video is currently inside.
 *
 * Driven by the match video rather than by the review tool's player, because
 * this list sits with the match video and a highlight that answered to a
 * different player would be a second opinion about where "now" is.
 */
function setTimelineNow(clockS) {
    const at = nowIndex(timelineRows, clockS);
    timelineRows.forEach((row, i) => row.el.classList.toggle('is-now', i === at));
}

export function renderTimeline(log, roster) {
    const list = byId('timeline');
    list.innerHTML = '';
    timelineRows = [];

    if (!log.length) {
        list.innerHTML = '<div class="empty">Nothing was tagged for this match.</div>';
        return;
    }

    const nameById = new Map(roster.map((r) => [r.id, r.playerName]));
    const { usName, themName } = teamLabels();

    for (const entry of log.slice().reverse()) {
        const clockS = entry.matchClockS;
        const row = timelineRow({
            clock: clockText(clockS),
            text: describeEvent(entry, {
                usName, themName, playerName: nameById.get(entry.playerId),
            }),
            sideLabel: entry.kind === 'period'
                ? ''
                : (entry.side === 'them' ? themName : usName),
            tone: timelineTone(entry),
            // Every tagged entry, not just the ones marked on the strip above.
            // The strip is deliberately thin — goals, cards and subs — because
            // eighty ticks is a texture; this list is where the restarts and
            // the fouls live, and each of them is a moment on the video too.
            onSeek: matchVideo ? () => seekMatchVideo(clockS) : undefined,
        });
        timelineRows.push({ clockS, el: row });

        if (entry.assistPlayerId && nameById.has(entry.assistPlayerId)) {
            const assist = document.createElement('span');
            assist.className = 'who';
            assist.textContent = ` (assist: ${nameById.get(entry.assistPlayerId)})`;
            row.querySelector('.tl-text').append(assist);
        }

        list.append(row);
    }
}

/**
 * Attach footage to a match, so players can jump to their own moments.
 *
 * The offset is the fiddly part and the reason it gets its own field rather
 * than being assumed zero: the match clock starts at kick-off and a recording
 * almost never does. Without it every marker lands during the warm-up, which
 * reads as the whole feature being broken rather than as one number being
 * unset.
 */
export async function doSaveVideo() {
    const button = byId('btn-save-video');
    const url = byId('input-video-url').value.trim();
    const offset = Number(byId('input-video-offset').value) || 0;
    const secondHalfVideoS = secondHalfInput();

    button.disabled = true;
    try {
        await updateMatch(state.team.id, state.match.id, {
            videoUrl: url || null,
            videoOffsetS: offset,
            secondHalfVideoS,
        });
        state.match.videoUrl = url || null;
        state.match.videoOffsetS = offset;
        state.match.secondHalfVideoS = secondHalfVideoS;

        // The players' copies. `publishReports` takes them at publish time, and
        // the ordinary order of events — publish after the match, upload the
        // footage that evening, paste the link — leaves every one of them
        // saying "no video for this match yet" forever. See pushVideoToReports.
        const reached = await pushVideoToReports(
            state.team.id, state.match.id, url || null, {
                videoOffsetS: offset,
                secondHalfVideoS,
                halfTimeClockS: state.match.halfTimeClockS ?? null,
            },
        );

        // The review tool below and the block above both embed this same video
        // and read this same offset. Without these they keep the old ones until
        // the page is reloaded, which reads as the link not having saved.
        renderMatchVideoBlock();
        // And the timeline, whose rows are only buttons when there is a player
        // to seek — pasting the first link is exactly the moment that changes.
        renderTimeline(state.match.log || [], state.match.roster || []);
        renderClockMap();
        if (!byId('cv-review-block').classList.contains('hidden')) renderReview();

        // One message, after the write rather than before it. A link we will
        // not embed is still saved — a Drive or Hudl link is worth giving a
        // player, it just cannot be seeked — so this says what happened rather
        // than refusing, and it says it once the writing is actually done.
        const added = reached
            ? `, and added to ${plural(reached, 'published report')}`
            : '';
        toast(url && !videoKind(url)
            ? `Saved${added}, but that link cannot be played inside PitchIQ.`
            : `Video link saved${added}`);
    } catch (err) {
        toast(err.message || 'Could not save the video link.', true);
    } finally {
        button.disabled = false;
    }
}
