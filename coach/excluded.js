// Who the video left out, and why.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { roughDuration } from '../shared/report.js?v=152';
import { byId, setText, plural } from '../shared/ui.js?v=152';
import { activeCv } from './shell.js?v=152';

// --------------------------------------------------- who was left out, and why
//
// The detector finds people, not players. A referee, a substitute warming up
// and somebody's dad on a folding chair all arrive as tracks, and
// pitchiq/participants.py drops or flags them before any number above is counted.
//
// That correction is invisible in the stats it corrects — a possession figure
// looks the same whether nine spectators were removed from it or none were. So
// it is shown here in full, with the sentence that produced each decision,
// because every threshold in that module is a guess that has never met a real
// touchline. A coach who can read "never moved more than 0.4 of a body length
// in 41 minutes" can tell a stationary parent from a wrongly-dropped keeper.
// A coach shown only "9 excluded" cannot.

const ROLE_TEXT = {
    offfield: ['Left out', 'Nothing above counts this figure.'],
    official: ['Kept, but unsure', 'Counted in everything above.'],
};

export function renderExcluded() {
    const section = byId('cv-excluded-block');
    if (!section) return;

    const cv = activeCv();
    const notes = cv?.participants || [];
    const quality = cv?.quality || {};
    // Both spellings, for the same reason cvConfidence takes both: the quality
    // block arrives snake_cased from Python, and the emulator fixtures write it
    // camelCased.
    const excluded = quality.excluded_tracks ?? quality.excludedTracks ?? 0;
    const officials = quality.flagged_officials ?? quality.flaggedOfficials ?? 0;

    // Hidden when there is nothing to report, and *also* when the run predates
    // this field — an empty list under a heading saying figures were left out
    // reads as a claim that none were, which is not something an older report
    // can support.
    if (!notes.length && !excluded && !officials) {
        section.classList.add('hidden');
        return;
    }
    section.classList.remove('hidden');

    setText('cv-excluded-note', excludedNote(excluded, officials, notes.length));

    const host = byId('cv-excluded');
    host.innerHTML = '';
    for (const note of notes) host.append(excludedRow(note));
}

function excludedNote(excluded, officials, shown) {
    const parts = [];
    if (excluded) {
        parts.push(`${plural(excluded, 'tracked figure')} left out of every `
            + 'number above — the pipeline judged them not to be playing.');
    }
    if (officials) {
        // The one that needs saying out loud. Without a calibration there is no
        // goalmouth to measure anyone against, so a referee and a goalkeeper
        // look identical on every feature available, and the pipeline keeps
        // both rather than risk deleting a player. That means a referee may be
        // inside the counts above.
        parts.push(`${plural(officials, 'figure')} moving like a player but `
            + 'matching neither kit — a referee, or your goalkeeper. They are '
            + 'still counted, because dropping a keeper is the worse mistake.');
    }
    if (!parts.length) parts.push('Nothing was left out of this run.');
    if (shown && shown < excluded + officials) {
        parts.push(`Showing the ${shown} that were on screen longest.`);
    }
    return parts.join(' ');
}

function excludedRow(note) {
    const [badge, effect] = ROLE_TEXT[note.role] || ['Left out', ''];

    const row = document.createElement('div');
    row.className = `list-item excluded-row is-${note.role}`;
    row.innerHTML = `
        <span class="excluded-badge"></span>
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>`;

    row.querySelector('.excluded-badge').textContent = badge;
    // "Track", never "Figure". The numbered figures above are *clusters*, and
    // an excluded track never became one — it is dropped before clustering
    // runs. Sharing the word would invite a coach to match "Figure 3" here to
    // "Figure 3" there, and they are two different numbering schemes that
    // happen to start at the same place.
    row.querySelector('.title').textContent = `Track ${note.trackId}`;
    // The pipeline's own sentence, unedited. Rewording it here would put a
    // second author between the threshold and the person judging it.
    row.querySelector('.sub').textContent = [
        note.reason,
        note.screenTimeS ? `on screen for ${roughDuration(note.screenTimeS)}` : '',
        effect,
    ].filter(Boolean).join(' · ');

    return row;
}
