import { onUser, signOut, configWarning } from '../assets/auth.js';
import { myReports } from '../assets/db.js';
import { CARD_COLOURS } from '../assets/events.js';

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

function statCard(value, label, tone = '') {
    const el = document.createElement('div');
    el.className = `stat ${tone}`;
    el.innerHTML = `<div class="value"></div><div class="label"></div>`;
    el.querySelector('.value').textContent = value;
    el.querySelector('.label').textContent = label;
    return el;
}

function renderSeason(reports) {
    const totals = reports.reduce((acc, r) => ({
        minutes: acc.minutes + (r.minutesPlayed || 0),
        goals: acc.goals + (r.goals || 0),
        assists: acc.assists + (r.assists || 0),
        yellow: acc.yellow + (r.yellowCards || 0),
        red: acc.red + (r.redCards || 0),
    }), { minutes: 0, goals: 0, assists: 0, yellow: 0, red: 0 });

    const grid = $('season-stats');
    grid.innerHTML = '';
    grid.appendChild(statCard(reports.length, 'Matches'));
    grid.appendChild(statCard(totals.minutes, 'Minutes played'));
    grid.appendChild(statCard(totals.goals, 'Goals', totals.goals ? 'is-good' : 'is-muted'));
    grid.appendChild(statCard(totals.assists, 'Assists', totals.assists ? 'is-good' : 'is-muted'));

    const cards = totals.yellow + totals.red;
    grid.appendChild(statCard(cards, 'Cards', cards ? 'is-warn' : 'is-muted'));

    // Goal involvements per 90 is the stat that survives uneven minutes, which
    // matters a lot for a squad player comparing themselves to a starter.
    if (totals.minutes >= 45) {
        const per90 = ((totals.goals + totals.assists) / totals.minutes * 90).toFixed(2);
        grid.appendChild(statCard(per90, 'G+A per 90', 'is-muted'));
    }
}

function figure(value, label) {
    const el = document.createElement('div');
    el.className = 'figure';
    el.innerHTML = `<div class="n"></div><div class="k"></div>`;
    const n = el.querySelector('.n');
    n.textContent = value;
    if (!value) n.classList.add('zero');
    el.querySelector('.k').textContent = label;
    return el;
}

function renderMatches(reports) {
    const list = $('match-list');
    list.innerHTML = '';

    for (const report of reports) {
        const row = document.createElement('div');
        row.className = 'list-item match-row';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <div class="match-figures"></div>`;

        row.querySelector('.title').textContent = `vs ${report.opponentName || '—'}`;

        const sub = row.querySelector('.sub');
        sub.textContent = report.matchDate || '';

        const cards = [
            ...Array(report.yellowCards || 0).fill('yellow'),
            ...Array(report.redCards || 0).fill('red'),
        ];
        if (cards.length) {
            const wrap = document.createElement('span');
            wrap.className = 'match-cards';
            for (const colour of cards) {
                const chip = document.createElement('span');
                chip.className = `card-chip ${colour}`;
                chip.title = CARD_COLOURS[colour]?.label ?? colour;
                wrap.appendChild(chip);
            }
            sub.appendChild(wrap);
        }

        const figures = row.querySelector('.match-figures');
        figures.appendChild(figure(report.minutesPlayed ?? 0, 'min'));
        figures.appendChild(figure(report.goals ?? 0, 'goals'));
        figures.appendChild(figure(report.assists ?? 0, 'assists'));

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
    $('player-number').textContent = latest.jerseyNumber ?? '—';
    $('player-sub').textContent = latest.teamName || '';

    renderSeason(reports);
    renderMatches(reports);
    show('view-reports');
}

function init() {
    const warning = configWarning();
    if (warning) $('config-slot').appendChild(warning);

    $('btn-signout').addEventListener('click', () =>
        signOut().then(() => { location.href = '../'; }));

    onUser((user) => {
        if (!user) { location.href = '../'; return; }
        load(user).catch((err) => {
            // A missing composite index surfaces here first, so name it rather
            // than showing a generic failure.
            const message = err?.code === 'failed-precondition'
                ? 'Still getting things ready. Please try again in a minute.'
                : err.message || 'Could not load your reports.';
            toast(message, true);
            show('view-empty');
            $('empty-msg').textContent = message;
        });
    });
}

init();
