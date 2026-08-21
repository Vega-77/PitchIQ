/**
 * The review tool: deciding whether what the pipeline found was really there.
 *
 * Lifted out of `coach.js` whole. It earns a file of its own the way a page
 * section does — it owns one piece of state (`reviewState`), it draws one
 * block of the match view, and the rest of the page reaches it through the ten
 * names exported below rather than through its internals.
 *
 * It calls back into the page at exactly two points, and those are registered
 * rather than imported: `coach.js` imports this module, so calling the other
 * way directly would be a cycle. See `onReviewChange`.
 */

import { saveCvReview, updateMatch } from '../assets/db.js?v=102';
import { EVENTS, describeEvent } from '../assets/events.js?v=102';
import {
    BY_CLOCK, BY_DOUBT, FROM_TAGGED, FROM_VIDEO, HALF_TIME, NOT_A_PLAYER,
    clockFromMatch, hasVerdict, orderCaveat, orderFeed, reviewFeed,
    reviewLabels, reviewScore,
} from '../assets/report.js?v=102';
import { renderStrip, timelineEnd } from '../assets/timeline.js?v=102';
import {
    byId, clockText, confidenceMark, plural, setText, toast,
} from '../assets/ui.js?v=102';
import { mount as mountVideo, videoKind } from '../assets/video.js?v=102';
import { download, matchXgTally, state, teamLabels } from './shell.js?v=102';

// Two things outside this module have to be redrawn when a verdict lands, and
// they are not the same thing. The shot views are drawn *from* the ledger, so
// they move when a decision changes it. The match rail carries how far the
// review has got, so it moves on every tick of progress whether or not the
// ledger did. Keeping the two apart is what stops a progress tick from
// redrawing the shot map.
let redrawLedgerViews = () => {};
let redrawProgressViews = () => {};

/** Told once, at start-up, what to redraw. Both are required. */
export function onReviewChange({ ledger, progress }) {
    redrawLedgerViews = ledger;
    redrawProgressViews = progress;
}

// ------------------------------------------------------- checking the video
//
// The pipeline produces candidates and this is where a human says whether they
// are real. Two halves, and both are needed:
//
//   - judging what it found gives precision. Of the 84 passes it claims, how
//     many happened?
//   - recording what it missed gives recall. Of the passes that happened, how
//     many did it find? Nothing in the pipeline's own output can answer that,
//     because a thing it never saw leaves no trace to disagree with.
//
// Recall is the number that decides whether the ball detector is good enough,
// so the "record a miss" half is not an extra.

export const REVIEW_TYPES = [
    'pass', 'carry', 'shot', 'tackle', 'interception', 'recovery',
    'clearance', 'duel',
];

const CONFIRMED = 'confirmed';
const REJECTED = 'rejected';
const EDITED = 'edited';

const reviewState = {
    filter: 'all', unreviewedOnly: false, inPlayOnly: false, video: null,
    // Match order by default. Every row seeks the video, so going in order is
    // one forward scrub through a half; going by doubt is a jump across ninety
    // minutes per verdict. The faster way to find mistakes is the slower way to
    // watch, and the coach picks.
    order: BY_CLOCK,
    // The strip is rebuilt every time a chip is tapped, so the playhead has to
    // be re-applied afterwards from somewhere. `atS` is the last position the
    // video reported, in footage seconds, or null if it has not said yet.
    strip: null, atS: null, stopClock: null,
};

/**
 * How many candidates fell inside a stoppage the tagged log knows about.
 *
 * Dead-ball events are stamped, never dropped — a throw-in is a real pass and a
 * coach counts it. But they are the events most likely to be junk: the ball is
 * stationary, players are walking, and the touch detector has nothing moving to
 * key on. So they are worth being able to set aside while reviewing, and worth
 * being able to look at on their own.
 *
 * Zero when no tagged log reached the run, because `inPlay` then defaults to
 * true on every event — which is an absence of information, not a match with no
 * stoppages in it. The filter hides itself in that case rather than offering a
 * control that cannot do anything.
 */
function deadBallCount() {
    const events = state.match?.cvEvents?.events || [];
    return events.filter((e) => e.inPlay === false).length;
}

export function renderReview() {
    const block = byId('cv-review-block');
    const events = state.match?.cvEvents?.events || [];

    // Absent for every match published before this tool existed. Hidden rather
    // than shown empty: an empty list reads as "the video found nothing".
    if (!events.length) {
        block.classList.add('hidden');
        return;
    }
    block.classList.remove('hidden');

    renderReviewVideo();
    renderConflicts();
    renderReviewFilters();
    renderReviewList();
    updateReviewProgress();
}

/**
 * The moments the tagged log and the video analysis contradict each other.
 *
 * Goals only, and above everything else in this block. Two independent records
 * of the same match disagreeing about a goal is the strongest signal either of
 * them produces — far stronger than a low-confidence pass the pipeline is
 * merely unsure about — and it takes a reviewer twenty seconds to settle.
 *
 * Hidden when the two agree, and hidden when there was no tagged log to compare
 * against. Those are different facts, but neither of them is something to put
 * on screen: the first is silence because nothing is wrong, and the second is
 * already said in the quality note above.
 */
function renderConflicts() {
    const host = byId('cv-conflicts');
    const entries = state.match?.cv?.reconciliation?.disagreements || [];
    host.innerHTML = '';
    host.classList.toggle('hidden', !entries.length);
    if (!entries.length) return;

    const heading = document.createElement('p');
    heading.className = 'conflicts-head';
    heading.textContent = plural(entries.length, 'goal')
        + ' the tagged log and the video disagree about';
    host.append(heading);

    for (const entry of entries) {
        const seconds = entry.status === 'tag_only' ? entry.tag_s : entry.cv_s;
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'conflict-row';

        const when = document.createElement('span');
        when.className = 'conflict-clock';
        when.textContent = clockAt(seconds);

        const what = document.createElement('span');
        what.textContent = entry.status === 'tag_only'
            ? 'tagged as a goal, but the video found no shot going in'
            : 'the video has a goal here that nobody tagged';

        row.append(when, what);
        row.addEventListener('click', () => seekReview(toMatchClock(seconds)));
        host.append(row);
    }
}

function renderReviewVideo() {
    const host = byId('cv-review-video');
    const url = state.match.videoUrl;

    leaveReview();
    host.innerHTML = '';

    if (url && videoKind(url)) {
        reviewState.video = mountVideo(host, url);
        setText('cv-review-note',
            'Tap any row to jump the video there.');
        // The other direction, and the reason the miss form no longer asks a
        // coach to read a number off the player: the video reports where it is,
        // and this is the one place that turns a position in the footage into a
        // reading on the match clock.
        reviewState.stopClock = reviewState.video.onTime((videoS) => {
            reviewState.atS = videoS;
            reviewState.strip?.setNow(toMatchClock(videoS));
            renderReviewNow();
        });
    } else {
        setText('cv-review-note', url
            ? 'That video link cannot be played inside PitchIQ, so the times '
              + 'below are readings rather than something to tap.'
            : 'Add a video link above and every row below becomes tappable.');
    }
    renderReviewNow();
}

/**
 * Where the video is, in the clock the form below it is asking for.
 *
 * Shown rather than only used, because the conversion is the whole point: a
 * coach who can see that 20:07 of footage is 18:07 of football can tell at a
 * glance whether the kick-off offset above is right, and a wrong offset is
 * otherwise invisible until every marker lands in the warm-up.
 */
function renderReviewNow() {
    const host = byId('cv-review-now');
    const button = byId('btn-missed-here');
    host.innerHTML = '';

    const at = reviewState.atS;
    const known = reviewState.video && at != null;
    button.classList.toggle('hidden', !known);
    if (!known) return;

    const { period } = clockMap().toClock(at);
    const clock = document.createElement('span');
    clock.className = 'now-clock';
    clock.textContent = clockAt(at);

    const what = document.createElement('span');
    // "half-time on the match clock" would be a reading that does not exist.
    // The interval has a position in the footage and no position in the match.
    what.textContent = period === HALF_TIME
        ? `— ${clockText(Math.round(at))} into the footage`
        : `on the match clock — ${clockText(Math.round(at))} into the footage`;

    host.append(clock, what);
}

/** Put the video's own position into the miss box, as a clock reading. */
export function useVideoPosition() {
    if (reviewState.atS == null) return;
    const { clockS, period } = clockMap().toClock(reviewState.atS);
    // Half-time is a real answer to "where is the video" and a useless one to
    // "when did this happen": every second of the interval reads as the same
    // second, so a miss recorded here would be filed at a moment the match was
    // not being played. Refuse rather than write down the frozen reading.
    if (period === HALF_TIME) {
        toast('The video is inside half-time — nothing to record there.', true);
        return;
    }
    byId('input-missed-clock').value = clockText(clockS);
}

/** Tear the embedded video down. Called when the match view is left. */
export function leaveReview() {
    reviewState.stopClock?.();
    reviewState.stopClock = null;
    reviewState.video?.destroy?.();
    reviewState.video = null;
    reviewState.atS = null;
    reviewState.strip = null;
}

function reviewSeek(clockS) {
    if (!reviewState.video) return;
    reviewState.video.seek(clockMap().toVideo(clockS));
    byId('cv-review-video').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/** This match's map between the footage and the clock. See clockFromMatch. */
export const clockMap = () => clockFromMatch(state.match);

/** What the clock read at a position in the footage. */
export function toMatchClock(timestampS) {
    return clockMap().toClock(timestampS || 0).clockS;
}

/**
 * That reading as a label — except during the break, where there isn't one.
 *
 * The clock froze at half-time, so every moment in the interval shares the
 * second the first half ended on. Printing that second is not wrong so much as
 * meaningless: three things drawn at 45:12 did not happen at the same time.
 * Only ever seen once the second-half anchor is set, since without it nothing
 * knows the break is there at all.
 */
export function clockAt(timestampS) {
    const { clockS, period } = clockMap().toClock(timestampS || 0);
    return period === HALF_TIME ? 'half-time' : clockText(clockS);
}

/** Both records of this match, merged and put on the clock. See `reviewFeed`. */
function reviewItems() {
    return reviewFeed(
        state.match?.cvEvents?.events || [],
        state.match?.log || [],
        { clock: clockMap(), missed: state.match?.cvReview?.missed || [] },
    );
}

/**
 * The merged feed, minus whatever the chips are hiding.
 *
 * A tagged row survives `all` and the `tagged` chip and nothing else. Filtering
 * to `pass` means "show me the passes it claims", and a corner is not one of
 * those — leaving the log in would make every type filter a mixed list. The
 * two toggles are about candidates by definition: a tagged entry has no verdict
 * to be missing, and its own `inPlay` is not a thing the log ever recorded.
 */
function visibleItems() {
    const decided = state.match?.cvReview?.byEvent || {};
    return orderFeed(reviewItems().filter((item) => {
        if (item.source === FROM_TAGGED) {
            return reviewState.filter === 'all' || reviewState.filter === FROM_TAGGED;
        }
        if (reviewState.filter === FROM_TAGGED) return false;
        const event = item.event;
        if (reviewState.filter !== 'all' && event.type !== reviewState.filter) return false;
        if (reviewState.unreviewedOnly && hasVerdict(decided[event.id])) return false;
        if (reviewState.inPlayOnly && event.inPlay === false) return false;
        return true;
    }), reviewState.order);
}

function renderReviewFilters() {
    const host = byId('cv-review-filters');
    host.innerHTML = '';

    const counts = state.match?.cvEvents?.counts || {};
    const handTagged = reviewItems()
        .filter((item) => item.source === FROM_TAGGED).length;
    const foundCount = (state.match.cvEvents.events || []).length;
    const options = [
        // Every chip counts what selecting it shows, and `all` shows both
        // records — so the tagged entries belong in this number. Counting only
        // the candidates put two totals for one list six lines apart on the
        // same screen: the chip read "Everything (433)" while the note under
        // the rows read "Showing the first 200 of 439". The six were the tagged
        // ones, sitting in the list, missing from the count above it.
        //
        // Deliberately not the same denominator as "n of m checked" above,
        // which is the candidates alone and correctly so: a tagged entry is a
        // human's own record of the match and has no verdict to give.
        ['all', `Everything (${foundCount + handTagged})`],
        ...REVIEW_TYPES
            .filter((type) => counts[type])
            .map((type) => [type, `${type} (${counts[type]})`]),
        // Last, and only when there is a log. These are not a kind of candidate
        // — they are the other record — so they sit apart from the type chips
        // rather than reading as one more thing the detector found.
        ...(handTagged ? [[FROM_TAGGED, `tagged by hand (${handTagged})`]] : []),
    ];

    for (const [value, label] of options) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.textContent = label;
        chip.classList.toggle('on', reviewState.filter === value);
        chip.addEventListener('click', () => {
            reviewState.filter = value;
            renderReviewFilters();
            renderReviewList();
        });
        host.append(chip);
    }

    const toggles = [
        ['Not checked yet', 'unreviewedOnly', true],
        // Only offered when the log actually marked some of these dead. See
        // deadBallCount() for why an absent log is not the same as none.
        [`Hide the ${deadBallCount()} in stoppages`, 'inPlayOnly', deadBallCount() > 0],
    ];

    for (const [label, key, show] of toggles) {
        if (!show) continue;
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.textContent = label;
        chip.classList.toggle('on', reviewState[key]);
        chip.addEventListener('click', () => {
            reviewState[key] = !reviewState[key];
            renderReviewFilters();
            renderReviewList();
        });
        host.append(chip);
    }

    // Order is not a filter — it changes nothing about which rows are here —
    // so it sits in its own group rather than as a seventh chip in a row of
    // things that hide events.
    const group = document.createElement('span');
    group.className = 'chip-group';
    const heading = document.createElement('span');
    heading.className = 'chip-group-label';
    heading.textContent = 'Work through';
    group.append(heading);

    for (const [value, label, title] of [
        [BY_CLOCK, 'in match order',
         'One forward scrub through the half — every row seeks the video'],
        [BY_DOUBT, 'least sure first',
         'The fastest way to find what the detector gets wrong, at the cost of '
         + 'jumping around the video and of a reviewed set that is deliberately '
         + 'the hard cases'],
    ]) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.textContent = label;
        chip.title = title;
        chip.classList.toggle('on', reviewState.order === value);
        chip.addEventListener('click', () => {
            reviewState.order = value;
            renderReviewFilters();
            renderReviewList();
            renderScorecard();
        });
        group.append(chip);
    }
    host.append(group);
}

function renderReviewList() {
    const host = byId('cv-review-list');
    host.innerHTML = '';

    const items = visibleItems();
    // The strip stays the pipeline's own. It is a picture of what the detector
    // found across the half, and salting it with human taps would make a run
    // that found nothing look busy.
    const marks = items
        .filter((item) => item.source === FROM_VIDEO)
        .map(({ event }) => ({
            id: event.id,
            clockS: toMatchClock(event.timestampS),
            type: event.type,
            label: `${event.type}${event.outcome ? ` (${event.outcome})` : ''}`,
        }));

    reviewState.strip = renderStrip(byId('cv-review-scrubber'), {
        marks,
        endS: timelineEnd([
            ...marks,
            ...(state.match.cvReview?.missed || []).map((m) => ({ clockS: m.clockS })),
        ]),
        onSeek: reviewSeek,
        clockText,
    });
    // A fresh strip knows nothing about where the video got to. Without this
    // the playhead vanishes on every filter tap and comes back a second later,
    // which reads as a flicker rather than as a rebuild.
    if (reviewState.atS != null) {
        reviewState.strip.setNow(toMatchClock(reviewState.atS));
    }

    if (!items.length) {
        host.innerHTML = '<div class="empty">Nothing matches that filter.</div>';
        return;
    }

    // A half can produce hundreds of these and the DOM cost of all of them at
    // once is real. The filters above are how you get to the rest.
    for (const item of items.slice(0, 200)) {
        host.append(item.source === FROM_TAGGED ? taggedRow(item) : reviewRow(item));
    }

    if (items.length > 200) {
        const more = document.createElement('div');
        more.className = 'empty';
        more.textContent =
            `Showing the first 200 of ${items.length}. Filter to see the rest.`;
        host.append(more);
    }
}

/**
 * One entry from the tagged log, sitting where it happened.
 *
 * Read-only on purpose, and visibly a different kind of thing. This is a
 * human's own record made at the time; there is no candidate here to confirm or
 * reject, and offering the buttons would invite a reviewer to "check" a fact
 * that was never in question and quietly imply it had been scored.
 *
 * The exception is a goal with nothing found near it, which is the one place
 * the log can tell the pipeline something: that is a miss, already proved, and
 * one tap records it instead of typing the clock back in from memory.
 */
function taggedRow(item) {
    const row = document.createElement('div');
    row.className = 'list-item review-row is-tagged';
    row.innerHTML = `
        <button type="button" class="review-seek">
            <span class="review-clock"></span>
            <span class="review-what"></span>
            <span class="review-who muted">tagged by hand</span>
        </button>
        <div class="review-acts"></div>`;

    row.querySelector('.review-clock').textContent = clockText(item.clockS);
    // The same wording the match timeline uses, from the same helper. Two
    // strips on one page naming the same team differently would read as two
    // different matches.
    row.querySelector('.review-what').textContent = describeEvent(item.entry, {
        ...teamLabels(),
        playerName: state.match?.roster
            ?.find((p) => p.id === item.entry.playerId)?.playerName,
    });
    row.querySelector('.review-seek')
        .addEventListener('click', () => reviewSeek(item.clockS));

    const acts = row.querySelector('.review-acts');
    if (item.suggestion) {
        row.classList.add('is-missed-goal');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn tiny';
        if (item.suggestion.recorded) {
            button.textContent = 'miss recorded';
            button.disabled = true;
        } else {
            button.textContent = 'the video missed this';
            button.addEventListener('click', () => recordMiss(
                item.suggestion.clockS, item.suggestion.type,
            ));
        }
        acts.append(button);
    }

    return row;
}

function reviewRow(item) {
    const event = item.event;
    const decided = state.match.cvReview.byEvent[event.id];
    const clockS = item.clockS;

    const row = document.createElement('div');
    row.className = 'list-item review-row';
    if (hasVerdict(decided)) row.classList.add(`is-${decided.status}`);
    row.innerHTML = `
        <button type="button" class="review-seek">
            <span class="review-clock"></span>
            <span class="review-what"></span>
            <span class="review-dead hidden">dead ball</span>
            <span class="review-near hidden"></span>
            <span class="review-who muted"></span>
        </button>
        <div class="review-mark"></div>
        <div class="review-acts">
            <button type="button" class="btn tiny" data-act="confirmed" title="Really happened">✓</button>
            <button type="button" class="btn tiny" data-act="edited" title="Wrong player or type">✎</button>
            <button type="button" class="btn tiny" data-act="rejected" title="Did not happen">✗</button>
        </div>
        <div class="review-edit hidden"></div>`;

    row.querySelector('.review-clock').textContent = clockAt(event.timestampS);
    row.querySelector('.review-what').textContent =
        decided?.type || event.type;
    row.querySelector('.review-who').textContent = whoIs(event.trackId);
    // Marked, not hidden. A pass from a throw-in is a real pass, and the coach
    // deciding whether this one happened should know the ball was not moving
    // when the detector claimed it did — that is the case it gets wrong most.
    if (event.inPlay === false) {
        row.querySelector('.review-dead').classList.remove('hidden');
    }
    // What a human said was happening within a few seconds of this. Almost
    // every hard judgement here is a question about context — a "pass" two
    // seconds after a throw-in is the throw — and until now answering it meant
    // scrolling to a different strip on a different part of the page.
    if (item.nearbyTag) {
        const near = row.querySelector('.review-near');
        near.classList.remove('hidden');
        near.textContent = `${tagLabel(item.nearbyTag.type)} ${gapWords(item.nearbyTag.gapS)}`;
    }
    row.querySelector('.review-mark').append(
        confidenceMark(confidenceBand(event.confidence)),
    );

    row.querySelector('.review-seek').addEventListener('click', () => reviewSeek(clockS));

    for (const button of row.querySelectorAll('[data-act]')) {
        button.classList.toggle('on', decided?.status === button.dataset.act);
        button.addEventListener('click', () => {
            if (button.dataset.act === EDITED) {
                toggleReviewEdit(row, event);
                return;
            }
            decide(event.id, { status: button.dataset.act });
            renderReviewList();
            updateReviewProgress();
        });
    }

    return row;
}

/** A tagged type as a coach would say it — "throw-in", not "throw_in". */
function tagLabel(type) {
    return EVENTS[type]?.label?.toLowerCase() || type.replace(/_/g, ' ');
}

/**
 * How far a tagged entry sits from a candidate, in words rather than a signed
 * number. "2s before" and "-2" are the same fact and only one of them can be
 * read at a glance while judging four hundred rows.
 */
function gapWords(gapS) {
    const seconds = Math.round(Math.abs(gapS));
    if (!seconds) return 'at the same moment';
    return `${seconds}s ${gapS < 0 ? 'before' : 'after'}`;
}

/**
 * Which player a tracked figure belongs to, going through the mapping above.
 *
 * Unmapped is the common case and says so plainly. Naming a guess here would
 * put a real student's name against an event nobody has agreed they were part
 * of, which is the one thing this whole feature is built to avoid.
 */
function whoIs(trackId) {
    const clusters = state.match?.cv?.identity?.clusters || [];
    const cluster = clusters.find((c) => (c.track_ids || []).includes(trackId));
    if (!cluster) return 'unknown figure';

    const playerId = state.match.cvMapping?.[String(cluster.cluster_id)];
    if (playerId === NOT_A_PLAYER) return 'ruled out as not a player';
    if (!playerId) return `figure ${cluster.cluster_id + 1}, unmatched`;

    const player = state.match.roster.find((p) => p.id === playerId);
    return player ? player.playerName : `figure ${cluster.cluster_id + 1}`;
}

function confidenceBand(value) {
    if (value >= 0.7) return 'high';
    if (value >= 0.45) return 'medium';
    return 'low';
}

function toggleReviewEdit(row, event) {
    const host = row.querySelector('.review-edit');
    if (!host.classList.contains('hidden')) {
        host.classList.add('hidden');
        host.innerHTML = '';
        return;
    }

    host.classList.remove('hidden');
    host.innerHTML = `
        <label class="field"><span>It was really a</span><select class="edit-type"></select></label>
        <label class="field"><span>by</span><select class="edit-who"></select></label>
        <button type="button" class="btn tiny edit-save">Save</button>`;

    const decided = state.match.cvReview.byEvent[event.id] || {};
    const typeSelect = host.querySelector('.edit-type');
    for (const type of REVIEW_TYPES) {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        typeSelect.append(option);
    }
    typeSelect.value = decided.type || event.type;

    const whoSelect = host.querySelector('.edit-who');
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'not sure';
    whoSelect.append(blank);
    for (const player of state.match.roster) {
        const option = document.createElement('option');
        option.value = player.id;
        option.textContent = player.jerseyNumber != null
            ? `${player.jerseyNumber} · ${player.playerName}`
            : player.playerName;
        whoSelect.append(option);
    }
    whoSelect.value = decided.playerId || '';

    host.querySelector('.edit-save').addEventListener('click', () => {
        decide(event.id, {
            status: EDITED,
            type: typeSelect.value,
            playerId: whoSelect.value || null,
        });
        renderReviewList();
        updateReviewProgress();
    });
}

function decide(eventId, verdict) {
    const next = { ...state.match.cvReview.byEvent };
    const before = next[eventId];
    // What a shot did, and what it was struck with, are separate answers from
    // whether it was a shot at all. Both survive every verdict here — including
    // a rejection, which takes the shot out of the xG check without throwing
    // away what the coach saw. Undoing a mis-tapped rejection therefore does not
    // mean marking the shot again.
    const kept = {};
    if (before?.result) kept.result = before.result;
    if (before?.header) kept.header = true;

    // Tapping the same verdict again clears it, so a mis-tap is one tap to fix
    // rather than a decision that cannot be taken back.
    if (before?.status === verdict.status && verdict.status !== EDITED) {
        // `kept` is an object either way, so this has to ask what is in it. It
        // did not, and an undone verdict left `{}` behind: an entry with no
        // verdict in it that every count still read as a checked event, and
        // that the "not checked yet" filter hid the row from for good.
        if (Object.keys(kept).length) next[eventId] = kept;
        else delete next[eventId];
    } else {
        next[eventId] = { ...kept, ...verdict };
    }
    state.match.cvReview = { ...state.match.cvReview, byEvent: next };
    queueReviewSave();
    // Rejecting a candidate up here takes it out of the xG check down there,
    // and off the shot map above if it was a tagged header. One rule, applied
    // at every point that writes to cvReview: the ledger changed, so redraw
    // everything drawn from it.
    redrawLedgerViews();
}

function updateReviewProgress() {
    const total = (state.match?.cvEvents?.events || []).length;
    const decided = Object.values(state.match?.cvReview?.byEvent || {})
        .filter(hasVerdict);
    const missed = (state.match?.cvReview?.missed || []).length;

    const real = decided.filter((d) => d.status !== REJECTED).length;
    const parts = [`${decided.length} of ${total} checked`];
    if (decided.length) {
        parts.push(`${Math.round((real / decided.length) * 100)}% of those were real`);
    }
    parts.push(missed
        ? `${plural(missed, 'miss', 'misses')} recorded`
        : 'no misses recorded yet');

    setText('cv-review-progress', parts.join(' · '));
    renderScorecard();
    redrawProgressViews();
}

/**
 * Precision and recall per event type, from the verdicts recorded so far.
 *
 * These are the two numbers this whole tool exists to produce, and until now
 * nothing computed either of them. They are also the two numbers easiest to
 * read as more than they are, so the caption is not decoration: everything here
 * describes **the events actually checked**. Precision over twelve of five
 * hundred is a fact about those twelve, and somebody who checked the twelve most
 * obvious ones has measured their own eye, not the detector.
 *
 * Recall is the one that decides whether the ball detector is good enough,
 * because a detector that finds six passes a half and gets all six right scores
 * perfectly on precision and is useless.
 */
function renderScorecard() {
    const host = byId('cv-scorecard');
    const events = state.match?.cvEvents?.events || [];
    const { byType, overall } = reviewScore(events, state.match?.cvReview);

    const rows = Object.entries(byType)
        .filter(([, s]) => s.truePositives + s.falsePositives + s.missed > 0)
        .sort((a, b) => b[1].truePositives + b[1].falsePositives
            - (a[1].truePositives + a[1].falsePositives));

    host.innerHTML = '';
    host.classList.toggle('hidden', !rows.length);
    if (!rows.length) return;

    // A dash, not 0%. Nothing has been checked of that type, and a zero would
    // read as a detector that gets everything wrong.
    const rate = (value) => (value == null ? '—' : `${Math.round(value * 100)}%`);

    const head = document.createElement('div');
    head.className = 'scorecard-row is-head';
    for (const text of ['', 'Right', 'Found', 'Checked']) {
        const cell = document.createElement('span');
        cell.textContent = text;
        head.append(cell);
    }
    host.append(head);

    for (const [type, s] of [...rows, ['Everything', overall]]) {
        const row = document.createElement('div');
        row.className = 'scorecard-row';
        row.classList.toggle('is-total', type === 'Everything');

        const checked = s.truePositives + s.falsePositives;
        const cells = [
            type,
            rate(s.precision),
            // Recall's denominator is what really happened, so it only means
            // anything once somebody has recorded a miss. Saying "100%" off the
            // back of no misses at all would be the most flattering possible
            // reading of no data.
            s.missed ? rate(s.recall) : '—',
            s.missed ? `${checked} · ${plural(s.missed, 'miss', 'misses')}`
                : String(checked),
        ];
        for (const text of cells) {
            const cell = document.createElement('span');
            cell.textContent = text;
            row.append(cell);
        }
        host.append(row);
    }

    const caption = document.createElement('p');
    caption.className = 'scorecard-note';
    caption.textContent = `Out of the ${overall.truePositives + overall.falsePositives}`
        + ` you have checked, not the ${events.length} the video found.`
        + (overall.missed
            ? ''
            : ' "Found" stays blank until you record something it missed —'
                + ' that is the half nothing else can tell you.');
    host.append(caption);

    // How the reviewed set was chosen, which the figures above cannot say for
    // themselves. Only while it is true.
    const bias = orderCaveat(
        reviewState.order, overall.truePositives + overall.falsePositives,
    );
    if (bias) {
        const note = document.createElement('p');
        note.className = 'scorecard-note is-warn';
        note.textContent = bias;
        host.append(note);
    }
}

/**
 * The reviewed set as a file on the coach's machine.
 *
 * Built and downloaded entirely in the browser: the data is already loaded, so
 * this needs no Firestore read, no rules change and no server. It is the same
 * approach as the tag-log download beside it.
 */
export function doDownloadLabels() {
    const labels = reviewLabels(
        state.match?.cvEvents?.events || [],
        state.match?.cvReview,
        {
            teamId: state.team.id,
            matchId: state.match.id,
            // The same two names the tag-log download uses. Both files come off
            // this one screen and describe the same match; spelling the match
            // two ways is how a pair of exports stops being joinable.
            opponentName: state.match.opponentName ?? null,
            matchDate: state.match.date ?? null,
            videoOffsetS: state.match.videoOffsetS ?? 0,
            secondHalfVideoS: state.match.secondHalfVideoS ?? null,
            halfTimeClockS: state.match.halfTimeClockS ?? null,
        },
    );

    if (!labels.labelled.length && !labels.missed.length) {
        toast('Nothing reviewed yet, so there is nothing to export.', true);
        return;
    }

    download(`pitchiq-labels-${state.match.id}.json`,
             JSON.stringify(labels, null, 2));

    toast(`Exported ${plural(labels.labelled.length, 'label')}`
        + ` and ${plural(labels.missed.length, 'miss', 'misses')}.`);
}

/** "12:30" or "750" to seconds. Returns null for anything else. */
function parseClock(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return null;

    const parts = trimmed.split(':');
    if (parts.length > 2 || parts.some((p) => !/^\d+$/.test(p))) return null;

    const seconds = parts.length === 2
        ? Number(parts[0]) * 60 + Number(parts[1])
        : Number(parts[0]);
    return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

export function doRecordMiss() {
    const clockS = parseClock(byId('input-missed-clock').value);
    if (clockS === null) {
        toast('Give the time as minutes and seconds, like 12:30.', true);
        return;
    }
    if (recordMiss(clockS, byId('input-missed-type').value)) {
        byId('input-missed-clock').value = '';
    }
}

/**
 * Add one thing the video did not find. Returns whether it went in.
 *
 * Shared by the typed form and by the one-tap button on a tagged goal, so both
 * routes produce the same record and hit the same cap. The button is the whole
 * point of merging the two records: the tagger already wrote down that a goal
 * happened at 34:12, and asking a coach to read that off one strip and retype
 * it into another is asking them to be a worse copy of a file that exists.
 */
function recordMiss(clockS, type) {
    const missed = [
        ...(state.match.cvReview.missed || []),
        { clockS, type, playerId: null },
    ].sort((a, b) => a.clockS - b.clockS);

    // The rules cap this at 300. Refuse here rather than letting the save fail
    // with a permission error that says nothing about what went wrong.
    if (missed.length > 300) {
        toast('That is 300 misses recorded — more than enough to judge by.', true);
        return false;
    }

    state.match.cvReview = { ...state.match.cvReview, missed };
    queueReviewSave();
    renderReviewList();
    updateReviewProgress();
    toast(`Recorded a missed ${type} at ${clockText(clockS)}.`);
    return true;
}

let reviewSaveTimer = null;
export function queueReviewSave() {
    clearTimeout(reviewSaveTimer);
    reviewSaveTimer = setTimeout(saveReviewNow, 600);
}

async function saveReviewNow() {
    // Two badges, one save. The review block and the shot log write the same
    // document from opposite ends of the page, and whichever one the coach is
    // looking at has to be the one that says it worked.
    const badges = ['cv-review-state', 'cv-shotlog-state']
        .map((id) => byId(id)).filter(Boolean);
    const say = (text) => badges.forEach((b) => { b.textContent = text; });

    try {
        say('Saving…');
        await saveCvReview(
            state.user, state.team.id, state.match.id, state.match.cvReview,
        );
        await saveXgCheck();
        say('Saved');
    } catch (err) {
        say('');
        toast(err.message || 'Could not save that.', true);
    }
}

/**
 * Roll this match's four-number tally onto its own document.
 *
 * Written here rather than at publish time because the season line has to move
 * as the marking is done — a coach who marks six shots and sees nothing change
 * anywhere has been given a button and no reason to press it again.
 *
 * Skipped whenever the tally is unchanged, which is most saves: the review
 * document is written on every verdict, and only the shot buttons move this.
 */
async function saveXgCheck() {
    const tally = matchXgTally();
    const before = state.match.xgCheck ?? null;
    const same = tally == null
        ? before == null
        : before != null
            && before.shots === tally.shots
            && before.scored === tally.scored
            && before.predicted === tally.predicted
            && before.variance === tally.variance;
    if (same) return;

    await updateMatch(state.team.id, state.match.id, { xgCheck: tally });
    state.match.xgCheck = tally;
    // The dashboard's copy of this match, so the season line is right the
    // moment a coach goes back — without re-reading anything.
    const listed = state.matches.find((m) => m.id === state.match.id);
    if (listed) listed.xgCheck = tally;
}
