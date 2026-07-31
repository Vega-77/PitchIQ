import { onUser, signOut, configWarning } from '../assets/auth.js?v=11';
import { myReports, seasonTotals } from '../assets/db.js?v=11';
import { CARD_COLOURS } from '../assets/events.js?v=11';
import {
    byId, setText, toast, showOnly, statCard, figure, cardChips,
} from '../assets/ui.js?v=11';

const VIEWS = ['view-empty', 'view-reports'];

function renderSeason(reports) {
    const totals = seasonTotals(reports);

    const grid = byId('season-stats');
    grid.innerHTML = '';
    grid.append(
        statCard(totals.matches, 'Matches'),
        statCard(totals.minutes, 'Minutes played'),
        statCard(totals.goals, 'Goals', totals.goals ? 'is-good' : 'is-muted'),
        statCard(totals.assists, 'Assists', totals.assists ? 'is-good' : 'is-muted'),
    );

    const cards = totals.yellowCards + totals.redCards;
    grid.append(statCard(cards, 'Cards', cards ? 'is-warn' : 'is-muted'));

    // Goal involvements per 90 is the stat that survives uneven minutes, which
    // matters a lot for a squad player comparing themselves to a starter.
    if (totals.minutes >= 45) {
        const per90 = ((totals.goals + totals.assists) / totals.minutes * 90).toFixed(2);
        grid.append(statCard(per90, 'G+A per 90', 'is-muted'));
    }
}

function renderMatches(reports) {
    const list = byId('match-list');
    list.innerHTML = '';

    for (const report of reports) {
        const row = document.createElement('div');
        row.className = 'list-item match-row';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <div class="figures"></div>`;

        row.querySelector('.title').textContent = `vs ${report.opponentName || '—'}`;

        const sub = row.querySelector('.sub');
        sub.textContent = report.matchDate || '';

        const chips = cardChips(report.yellowCards, report.redCards, CARD_COLOURS);
        if (chips.length) {
            const wrap = document.createElement('span');
            wrap.className = 'match-cards';
            wrap.append(...chips);
            sub.append(wrap);
        }

        row.querySelector('.figures').append(
            figure(report.minutesPlayed ?? 0, 'min'),
            figure(report.goals ?? 0, 'goals'),
            figure(report.assists ?? 0, 'assists'),
        );

        list.append(row);
    }
}

async function loadReports(user) {
    setText('who', user.email);

    const reports = await myReports(user.uid);
    if (!reports.length) {
        showOnly('view-empty', VIEWS);
        return;
    }

    const latest = reports[0];
    setText('player-name', latest.playerName || 'My season');
    setText('player-number', latest.jerseyNumber ?? '—');
    setText('player-sub', latest.teamName || '');

    renderSeason(reports);
    renderMatches(reports);
    showOnly('view-reports', VIEWS);
}

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    byId('btn-signout').addEventListener('click', () =>
        signOut().then(() => { location.href = '../'; }));

    onUser((user) => {
        if (!user) { location.href = '../'; return; }
        loadReports(user).catch((err) => {
            // A missing composite index surfaces here first, so name it rather
            // than showing a generic failure.
            const message = err?.code === 'failed-precondition'
                ? 'Still getting things ready. Please try again in a minute.'
                : err.message || 'Could not load your reports.';
            toast(message, true);
            showOnly('view-empty', VIEWS);
            setText('empty-msg', message);
        });
    });
}

init();
