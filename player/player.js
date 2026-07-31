// A player's own season.
//
// This is the only page a player ever sees, and for most of them it is the
// whole product. So it tries to be worth opening more than once: not just the
// totals, but the shape of the season — whether their minutes are climbing,
// which match was their best, how the run of games has gone.
//
// Everything here comes from playerReports documents, which the coach writes at
// publish time. There is no live match data on this page by design; see the
// note on collection-group rules in firestore.rules.

import { onUser, signOut, configWarning } from '../assets/auth.js?v=14';
import { myReports, seasonTotals } from '../assets/db.js?v=14';
import { CARD_COLOURS } from '../assets/events.js?v=14';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=14';
import {
    byId, setText, toast, showOnly, statCard, figure, cardChips, plural,
    minutesChart,
} from '../assets/ui.js?v=14';

const VIEWS = ['view-empty', 'view-reports'];

// A full match, for working out whether someone played most of one.
const FULL_MATCH_MIN = 80;

const involvements = (r) => (r.goals || 0) + (r.assists || 0);

// ---------------------------------------------------------------- headline

/**
 * One sentence about the season, so the page opens by telling them something
 * rather than by handing them a grid to interpret.
 */
function seasonLine(reports, totals) {
    if (!reports.length) return '';

    const scoring = reports.filter((r) => involvements(r) > 0).length;
    const opening = `${plural(totals.matches, 'match', 'matches')} played`;

    if (!totals.goals && !totals.assists) {
        return `${opening}, ${totals.minutes} minutes on the field.`;
    }

    const tally = [];
    if (totals.goals) tally.push(plural(totals.goals, 'goal'));
    if (totals.assists) tally.push(plural(totals.assists, 'assist'));

    // "in 7 of them" rather than "across 7 matches" — the sentence already
    // opened with a match count, and saying it twice reads as a mistake.
    const spread = scoring > 1 ? ` — involved in ${scoring} of them` : '';
    return `${opening}, ${tally.join(' and ')}${spread}.`;
}

function renderSeason(reports) {
    const totals = seasonTotals(reports);

    setText('player-line', seasonLine(reports, totals));

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

    // Their best afternoon. Worth naming — it is the thing a player actually
    // wants to find on their own page.
    const best = reports.reduce(
        (a, b) => (involvements(b) > involvements(a) ? b : a), reports[0]
    );
    if (involvements(best) > 0) {
        grid.append(statCard(
            involvements(best),
            `Best · ${best.opponentName || 'opponent'}`,
            'is-good',
        ));
    }

    const full = reports.filter((r) => (r.minutesPlayed || 0) >= FULL_MATCH_MIN).length;
    if (full) grid.append(statCard(full, 'Full matches', 'is-muted'));
}

// ---------------------------------------------------------------- the season

/** The season as a shape. The chart itself is shared with the coach's view. */
function renderChart(reports) {
    const host = byId('minutes-chart');
    host.innerHTML = '';
    host.append(minutesChart(reports));

    const scored = reports.filter((r) => involvements(r)).length;
    setText('chart-note', scored
        ? 'The line is 90 minutes, oldest match on the left. Highlighted bars are '
          + `matches you scored or assisted in — ${plural(scored, 'match', 'matches')}.`
        : 'The line is 90 minutes, oldest match on the left.');
}

// ---------------------------------------------------------------- matches

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

// ---------------------------------------------------------------- load

function showEmpty(message) {
    mountPitchBackdrop(byId('empty-hero'), { opacity: 0.16 });
    if (message) setText('empty-msg', message);
    showOnly('view-empty', VIEWS);
}

async function loadReports(user) {
    setText('who', user.email);

    const reports = await myReports(user.uid);
    if (!reports.length) return showEmpty();

    const latest = reports[0];
    mountPitchBackdrop(byId('player-hero'), { opacity: 0.16 });
    setText('player-name', latest.playerName || 'My season');
    setText('player-number', latest.jerseyNumber ?? '—');
    setText('player-sub', latest.teamName || 'Your season');

    renderSeason(reports);
    renderChart(reports);
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
            showEmpty(message);
        });
    });
}

init();
