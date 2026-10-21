// The shots: the maps, the shot log a coach marks the model's homework in,
// the xG check, and the redraw every surface the review feeds goes through.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { renderShotMap, shotSummary } from '../shared/shot-map.js?v=148';
import { SAMPLE_NOTICE, isSample } from '../shared/sample-report.js?v=148';
import {
    xgTrust, SHOT_RESULTS, shotLedger, sumXgTallies, xgCalibration, calibrationNote,
    headerCorrection, headerNote, correctedShotMarks,
} from '../shared/report.js?v=148';
import { byId, setText, plural } from '../shared/ui.js?v=148';
import { activeCv, matchXgTally, state } from './shell.js?v=148';
import { clockAt, queueReviewSave, toMatchClock } from './review.js?v=148';
import { seekMatchVideo } from './footage.js?v=148';
import { redrawCvNote, renderPlayerTable, mergeCvPlayers } from './match-overview.js?v=148';
import { renderPassing } from './match-play.js?v=148';

/**
 * Both sides' shots, side by side.
 *
 * Each map is mirrored so that team attacks right, which is what makes them
 * comparable: two maps facing each other would be a diagram of the pitch, and
 * the question a coach has is "how good were the chances", not "which end".
 * The mirroring is done in pitchiq/report_export.py so no renderer can forget it.
 *
 * Clicking a shot seeks the match video, which is the whole reason to place
 * them rather than count them.
 */
export function renderShots() {
    const block = byId('cv-shots-block');
    const cv = activeCv();

    const sides = [
        ['us', 'team_a', 'cv-shots-us', 'cv-shots-us-cap'],
        ['them', 'team_b', 'cv-shots-them', 'cv-shots-them-cap'],
    ];

    const trust = xgTrust(cv?.calibrationErrorM);
    // Any shot the coach has tagged as a header is redrawn and re-totalled at
    // its header xG. Applied here rather than only in the log below so the map,
    // its caption and the check underneath all say the same thing — two totals
    // for the same shots, one corrected and one not, is worse than neither.
    const ledger = shotLedger(
        state.match?.cvEvents?.events || [], state.match?.cvReview,
    );

    let any = false;
    for (const [label, key, hostId, capId] of sides) {
        const marks = correctedShotMarks(cv?.teams?.[key]?.shot_map || [], ledger);
        const drawn = renderShotMap(byId(hostId), marks, {
            onPick: (mark) => seekMatchVideo(toMatchClock(mark.video_s)),
            label: `${label === 'us' ? 'Our' : 'Their'} shots on the pitch`,
            xgTrust: trust,
        });
        any = any || drawn;

        const totals = shotSummary(marks, trust);
        const who = label === 'us'
            ? (state.team?.name || 'Us')
            : (state.match?.opponentName || 'Them');
        setText(capId, drawn
            ? `${who} — ${totals.goals} from ${plural(totals.shots, 'shot')}`
                + (totals.xg != null ? `, ${totals.xg.toFixed(2)} xG` : '')
            : `${who} — no shots placed`);
        byId(hostId).classList.toggle('is-empty', !drawn);
        // Marked on the plot itself, not only in the banner above it. These get
        // screenshotted and pasted somewhere with no banner in the crop.
        byId(hostId).classList.toggle('is-sample-plot', drawn && isSample(cv));
    }

    block.classList.toggle('hidden', !any);
    if (!any) return;

    // The sentence about circle size only holds while the circles have one.
    // "Click one to jump the video there" is markup, not text: it is false on
    // paper, and setText writes textContent so it cannot carry its own class.
    const corrected = headerNote(headerCorrection(ledger));
    setText('cv-shots-note',
        ('Both halves are drawn attacking right, so the two maps can be compared. '
            + (trust === 'shot'
                ? 'Bigger circles were better chances. '
                : 'Every shot is drawn the same size — the calibration is too loose '
                    + 'to rank them against each other. ')
            + (corrected || '')).trim());
}

// ---------------------------------------------- marking the model's homework
//
// Every xG on the map above is a prediction, and nothing in this app has ever
// checked one. The model was fitted on professional shots; a high school pitch
// is a different game played by different bodies, and whether the number
// transfers is an empirical question nobody has asked.
//
// So a coach says what each detected shot did, and the arithmetic in
// shared/report.js compares that to what the model said. The two design rules
// live there; what matters here is the second one, because it decides the
// layout: on a dozen shots the answer is always "cannot tell", and a card that
// only ever says that would be switched off. Hence the season line on the
// matches tab. The per-match card exists to be filled in, not to be believed.

/**
 * Every shot the pipeline found, with what a coach says became of it.
 *
 * Built from the event list rather than the shot map. The map has positions and
 * no ids, and an id is what lets a verdict outlive the page — but it also means
 * a shot dropped from a truncated event list is missing here, which is said out
 * loud rather than left to be discovered.
 *
 * Real runs only. The sample carries no events, so this stays hidden under the
 * preview on its own — the same boundary the picker and the review tool keep,
 * and for the same reason: a verdict tapped against an invented id would write a
 * decision about nothing into a real document.
 */
function renderShotLog() {
    const block = byId('cv-shotlog-block');
    const rows = shotLedger(
        state.match?.cvEvents?.events || [], state.match?.cvReview,
    );

    block.classList.toggle('hidden', !rows.length);
    if (!rows.length) return;

    // Counted over the shots that still are ones, so this reads the same as the
    // card below it. Two counts with different denominators sitting a line apart
    // is one claim as far as anyone reading them is concerned.
    const live = rows.filter((r) => r.counted);
    const marked = live.filter((r) => r.result != null).length;
    const dropped = rows.length - live.length;
    const truncated = state.match?.cvEvents?.truncated;
    setText('cv-shotlog-note',
        `${marked} of ${plural(live.length, 'shot')} marked. `
        + 'What the video made of each one is printed beside it and is not an '
        + 'answer to this — it is read off a ball the pipeline only sees in '
        + 'some frames, and grading the model against another guess measures '
        + 'nothing. '
        // The other half of the row, and the one that changes a published
        // figure rather than adding to it. The instruction to tap it is in the
        // markup instead: it is false on paper, and setText writes textContent,
        // so a sentence in here cannot carry its own class.
        + 'One fixed camera cannot see the ball\'s height, so every shot '
        + 'above is scored as if it were struck with the foot, and that '
        + 'overstates a headed chance by a third to three times over.'
        + (dropped
            ? ` ${plural(dropped, 'candidate')} struck out below: you rejected `
              + 'them as something other than a shot, so they count for nothing '
              + 'here.'
            : '')
        + (truncated
            ? ' The event list was trimmed to the most confident candidates, so '
              + 'some shots may not be here.'
            : ''));

    const host = byId('cv-shotlog');
    host.innerHTML = '';
    for (const row of rows) host.append(shotLogRow(row));

    renderXgCheck();
}

function shotLogRow(row) {
    const clockS = toMatchClock(row.timestampS);
    const trust = xgTrust(state.match?.cv?.calibrationErrorM);

    const item = document.createElement('div');
    item.className = 'list-item shot-row';
    // Struck through rather than removed. A row that vanishes the moment you
    // reject it in the block below looks like a bug, and this is the only thing
    // on screen that explains why the tally just moved.
    item.classList.toggle('is-out', !row.counted);
    item.classList.toggle('is-header', row.header);
    item.innerHTML = `
        <button type="button" class="shot-seek">
            <span class="shot-clock"></span>
            <span class="shot-side"></span>
            <span class="shot-xg"></span>
            <span class="shot-guess muted"></span>
        </button>
        <div class="shot-results"></div>
        <div class="shot-part">
            <button type="button" class="btn tiny shot-header-btn"
                    title="Score this as a header instead of a foot shot">Header</button>
        </div>`;

    item.querySelector('.shot-clock').textContent = clockAt(row.timestampS);
    item.querySelector('.shot-side').textContent = row.team === 'team_b'
        ? (state.match?.opponentName || 'Them')
        : (state.team?.name || 'Us');
    // Withheld on exactly the bands the shot map withholds a radius. A per-shot
    // number this page has stopped drawing but still prints beside the buttons
    // would be the same claim made quietly.
    item.querySelector('.shot-xg').textContent =
        (trust === 'shot' && row.xg != null) ? `${row.xg.toFixed(2)} xG` : '';
    item.querySelector('.shot-guess').textContent = row.counted
        ? shotAside(row)
        : 'not a shot, per the review below';

    item.querySelector('.shot-seek')
        .addEventListener('click', () => seekMatchVideo(clockS));

    const headerButton = item.querySelector('.shot-header-btn');
    headerButton.classList.toggle('on', row.header);
    headerButton.addEventListener('click', () => toggleHeader(row.id));

    const results = item.querySelector('.shot-results');
    for (const { value, label } of SHOT_RESULTS) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn tiny';
        button.textContent = label;
        button.classList.toggle('on', row.result === value);
        button.addEventListener('click', () => markShot(row.id, value));
        results.append(button);
    }

    return item;
}

/**
 * The quiet line beside each shot: what the video read, and what a header tag
 * did to the number.
 *
 * The movement is shown rather than only the new figure. A coach who taps
 * "Header" and watches 0.72 become 0.43 has learnt something real about how the
 * model sees the game; one who just sees 0.43 has watched a number change for
 * no stated reason.
 */
function shotAside(row) {
    if (row.header) {
        if (row.xgHeader == null) {
            // Deliberately dropped rather than scored as a foot shot. Doing
            // that anyway is precisely the error the tag exists to fix.
            return 'scored as a header — this run predates that reading, so it '
                + 'is left out of the check below';
        }
        const was = row.xgFoot == null ? null : row.xgFoot.toFixed(2);
        return `scored as a header${was ? ` — ${was} off the foot` : ''}`;
    }
    return row.guessed ? `video read it as ${row.guessed.replace(/_/g, ' ')}` : '';
}

/**
 * Say a shot was headed, or take it back.
 *
 * The one thing on this page that changes a number the pipeline produced, which
 * is why it is stored in cvReview beside the coach's other judgements and never
 * written back over `cvStats`. A correction has to stay distinguishable from a
 * measurement; the shot map applies it at render time instead.
 */
function toggleHeader(eventId) {
    const byEvent = { ...state.match.cvReview.byEvent };
    const before = byEvent[eventId] || {};
    const next = { ...before, header: !before.header };

    if (!next.header && next.result == null && !next.status) delete byEvent[eventId];
    else byEvent[eventId] = next;

    state.match.cvReview = { ...state.match.cvReview, byEvent };
    queueReviewSave();
    // The map, its caption and the quality banner are all drawn from these
    // tags, so they move with them.
    redrawShotViews();
}

/**
 * Record — or clear — what a shot did.
 *
 * Merged into whatever verdict the review tool already holds for this event
 * rather than replacing it. The two are different questions: "was that a shot"
 * and "what did it do", and answering one must not silently unanswer the other.
 */
function markShot(eventId, result) {
    const byEvent = { ...state.match.cvReview.byEvent };
    const before = byEvent[eventId] || {};
    // Tapping the same answer again clears it, matching the review buttons —
    // a mis-tap is one tap to fix rather than a mark that cannot be taken back.
    const next = before.result === result
        ? { ...before, result: null }
        : { ...before, result };

    if (next.result == null && !next.status && !next.header) delete byEvent[eventId];
    else byEvent[eventId] = next;

    state.match.cvReview = { ...state.match.cvReview, byEvent };
    queueReviewSave();
    renderShotLog();
}

function renderXgCheck() {
    const host = byId('cv-xg-check');
    const cal = xgCalibration(matchXgTally());

    host.innerHTML = '';
    host.classList.toggle('hidden', !cal.shots);
    if (!cal.shots) return;

    host.className = `xg-check is-${cal.verdict}`;

    const figures = document.createElement('div');
    figures.className = 'xg-check-figures';
    const pairs = [
        [cal.predicted.toFixed(2), 'the model expected'],
        [String(cal.scored), cal.scored === 1 ? 'goal went in' : 'goals went in'],
        [`±${cal.band.toFixed(1)}`, 'chance alone, on this many shots'],
    ];
    for (const [value, label] of pairs) {
        const cell = document.createElement('div');
        cell.innerHTML = '<b></b><span></span>';
        cell.querySelector('b').textContent = value;
        cell.querySelector('span').textContent = label;
        figures.append(cell);
    }
    host.append(figures);

    const note = document.createElement('p');
    note.className = 'xg-check-note';
    note.textContent = calibrationNote(cal);
    host.append(note);
}

/**
 * The same check across every match, from tallies already in hand.
 *
 * `listMatches` returns whole match documents, so this costs nothing beyond the
 * read the list needed anyway. That is the whole reason the tally is stored
 * there rather than recomputed: the alternative is reading every match's review
 * document on every dashboard load, to answer a question that changes once a
 * fortnight.
 */
export function renderSeasonXgCheck() {
    const host = byId('season-xg');
    if (!host) return;

    const cal = xgCalibration(sumXgTallies(state.matches.map((m) => m.xgCheck)));
    host.classList.toggle('hidden', !cal.shots);
    if (!cal.shots) return;

    setText('season-xg-note',
        calibrationNote(cal, { over: 'marked across the season' }));
}

/**
 * The control that turns the sample on, and what it says.
 *
 * Offered only when this match has no real run. A preview button sitting beside
 * genuine figures is an invitation to mix the two up, and the case it exists
 * for — "there is nothing here and I cannot tell whether that is right" — only
 * arises when there is nothing here.
 */
export function renderSampleToggle() {
    const row = byId('cv-sample-toggle');
    if (!row) return;

    const offerable = !state.match?.cv;
    row.classList.toggle('hidden', !offerable);
    row.classList.toggle('is-on', state.cvPreview);
    if (!offerable) return;

    byId('btn-cv-sample').textContent = state.cvPreview
        ? 'Hide the sample'
        : 'Preview with sample data';
    setText('cv-sample-hint', state.cvPreview
        ? SAMPLE_NOTICE
        : 'See what the video-derived sections look like, using made-up numbers.');
}

/**
 * Everything the review feeds, redrawn together.
 *
 * Five surfaces read it — the shot maps, their caption, the quality banner, the
 * log with its check, and now the player table — and they are spread from the
 * top of the page to the bottom. Redrawing a subset is how they end up
 * contradicting each other, which is exactly what happened twice before:
 * rejecting a tagged header in the review block left the map still showing the
 * correction, and tagging one left the banner still saying nothing had been
 * tagged. It happened a third time the day the player table started reading the
 * review — a rejected tackle vanished from the scorecard and stayed in the
 * player's row.
 *
 * Cheap enough to do wholesale — a match has a dozen shots and a squad, not a
 * thousand of either.
 */
export function redrawShotViews() {
    redrawCvNote();
    renderShots();
    renderShotLog();
    // Not shots, but drawn from the same mapping the picker writes and the same
    // event list the review tool judges — so it goes stale in exactly the same
    // places, and belongs on the same redraw.
    renderPassing();
    // The per-player figures now carry the coach's verdicts, so the table is a
    // view of the review like every other surface above.
    if (state.match?.stats?.players) {
        mergeCvPlayers(
            state.match.stats.players, state.match, state.match.stats.matchEndS,
        );
        renderPlayerTable(state.match.stats.players);
    }
}
