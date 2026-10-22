// The Matches tab: the list of matches, and adding one.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { listMatches, createMatch } from '../shared/db.js?v=154';
import { byId, toast, localDate } from '../shared/ui.js?v=154';
import { state } from './shell.js?v=154';
import { renderSeasonXgCheck } from './match-shots.js?v=154';
import { openMatch } from './match.js?v=154';

// ---------------------------------------------------------------- matches

export function renderMatches() {
    const list = byId('match-list');
    list.innerHTML = '';

    renderSeasonXgCheck();

    if (!state.matches.length) {
        list.innerHTML =
            '<div class="empty">No matches yet.<br>'
            + '<span class="muted">Add one with the New match form, then tag it live from the touchline.</span></div>';
        return;
    }

    for (const match of state.matches) {
        list.append(matchRow(match));
    }
}

function matchRow(match) {
    const row = document.createElement('button');
    row.className = 'list-item';
    row.innerHTML = `
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>
        <span class="match-score hidden"></span>
        <span class="pill"></span>`;

    row.querySelector('.title').textContent = `vs ${match.opponentName || '—'}`;
    row.querySelector('.sub').textContent = match.date || 'no date';

    const pill = row.querySelector('.pill');

    // A finished match shows its result, colour-coded, instead of just a status
    // word — that is the thing a coach is actually scanning for.
    if (match.finalized) {
        const us = match.scoreUs ?? 0;
        const them = match.scoreThem ?? 0;
        const score = row.querySelector('.match-score');
        score.textContent = `${us}–${them}`;
        score.classList.remove('hidden');
        score.classList.add(us > them ? 'win' : us < them ? 'loss' : 'draw');

        pill.textContent = 'published';
        pill.classList.add('done');
    } else if (['first_half', 'second_half'].includes(match.status)) {
        pill.textContent = 'live';
        pill.classList.add('live');
    } else {
        pill.textContent = (match.status || 'scheduled').replace(/_/g, ' ');
    }

    row.addEventListener('click', () => openMatch(match.id));
    return row;
}

export async function doCreateMatch() {
    const opponentName = byId('input-opponent').value.trim();
    const date = byId('input-date').value || localDate();
    if (!opponentName) return toast('Who are you playing?', true);

    try {
        await createMatch(state.user, state.team.id, { opponentName, date });
        byId('input-opponent').value = '';
        state.matches = await listMatches(state.team.id);
        renderMatches();
        toast('Match created');
    } catch (err) {
        toast(err.message || 'Could not create the match.', true);
    }
}
