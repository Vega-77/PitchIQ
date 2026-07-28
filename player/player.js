import { onUser, signOut, configWarning } from '../assets/auth.js';
import { myReports } from '../assets/db.js';

const $ = (id) => document.getElementById(id);

let toastTimer;
function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2800);
}

function show(view) {
    for (const v of ['view-empty', 'view-reports']) {
        $(v).classList.toggle('hidden', v !== view);
    }
    $('loading').classList.add('hidden');
}

function statCard(value, labelText) {
    const el = document.createElement('div');
    el.className = 'stat';
    el.innerHTML = `<div class="value"></div><div class="label"></div>`;
    el.querySelector('.value').textContent = value;
    el.querySelector('.label').textContent = labelText;
    return el;
}

function renderSeason(reports) {
    const totals = reports.reduce(
        (acc, r) => ({
            minutes: acc.minutes + (r.minutesPlayed || 0),
            goals: acc.goals + (r.goals || 0),
            cards: acc.cards + (r.cards || 0),
        }),
        { minutes: 0, goals: 0, cards: 0 }
    );

    const grid = $('season-stats');
    grid.innerHTML = '';
    grid.appendChild(statCard(reports.length, 'Matches'));
    grid.appendChild(statCard(totals.minutes, 'Minutes played'));
    grid.appendChild(statCard(totals.goals, 'Goals'));
    grid.appendChild(statCard(totals.cards, 'Cards'));
}

function renderMatches(reports) {
    const list = $('match-list');
    list.innerHTML = '';

    for (const report of reports) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <div style="display:flex;gap:18px;text-align:right">
                <div><div class="jersey mins"></div><div class="sub">min</div></div>
                <div><div class="jersey goals"></div><div class="sub">goals</div></div>
            </div>`;

        row.querySelector('.title').textContent = `vs ${report.opponentName || '—'}`;
        row.querySelector('.sub').textContent = report.matchDate || '';
        row.querySelector('.mins').textContent = report.minutesPlayed ?? 0;
        row.querySelector('.goals').textContent = report.goals ?? 0;

        list.appendChild(row);
    }
}

async function load(user) {
    $('who').textContent = user.email;

    const reports = await myReports(user.uid);

    if (!reports.length) {
        show('view-empty');
        return;
    }

    const latest = reports[0];
    $('player-name').textContent = latest.playerName || 'My season';
    $('player-sub').textContent = [
        latest.teamName,
        latest.jerseyNumber != null ? `#${latest.jerseyNumber}` : null,
    ].filter(Boolean).join(' · ');

    renderSeason(reports);
    renderMatches(reports);
    show('view-reports');
}

function init() {
    const warning = configWarning();
    if (warning) $('config-slot').appendChild(warning);

    $('btn-signout').addEventListener('click', () => signOut().then(() => {
        location.href = '../';
    }));

    onUser((user) => {
        if (!user) { location.href = '../'; return; }
        load(user).catch((err) => {
            // A missing index surfaces here first, so name it explicitly rather
            // than showing a generic failure.
            const msg = err?.code === 'failed-precondition'
                ? 'The reports index is still building. Try again in a minute.'
                : err.message || 'Could not load your reports.';
            toast(msg, true);
            show('view-empty');
            $('empty-msg').textContent = msg;
        });
    });
}

init();
