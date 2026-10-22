// One player's season, as their coach sees it.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { playerSeason, seasonTotals, knownMinutes } from '../shared/db.js?v=152';
import {
    seasonForms, formNote, MIN_FORM_POINTS, MIN_POINT_MINUTES,
} from '../shared/season.js?v=152';
import { renderForms } from '../shared/form-chart.js?v=152';
import { positionLabel, FULL_MATCH_MIN, seasonGroups } from '../shared/report.js?v=152';
import { CARD_COLOURS } from '../shared/events.js?v=152';
import { mountRail } from '../shared/rail.js?v=152';
import {
    byId, setText, toast, plural, fillStatGroup, figure, cardChips, minutesChart, coverageStrip,
} from '../shared/ui.js?v=152';
import { show, state } from './shell.js?v=152';

// ---------------------------------------------------------------- one player

export async function openPlayer(player) {
    byId('loading').classList.remove('hidden');

    try {
        const reports = await playerSeason(state.team.id, state.matches, player.id);
        const totals = seasonTotals(reports);

        setText('pv-number', player.jerseyNumber ?? '—');
        setText('pv-name', player.name);
        setText('pv-sub', [positionLabel(player.position),
            player.emailLower || 'no email on file'].filter(Boolean).join(' · '));

        const linked = byId('pv-linked');
        const isLinked = Boolean(player.linkedUid);
        linked.textContent = isLinked ? 'Can see reports' : 'Not joined yet';
        linked.classList.toggle('done', isLinked);

        // The same four groups the player sees on their own page, from the same
        // function. Two piles of boxes built twice had already drifted — one
        // called it "Tackles" and the other "Tackles won", one counted carries
        // and the other had never heard of them — on the one season a coach and
        // a player look at together.
        const groups = new Map(
            seasonGroups(reports, totals).map((group) => [group.id, group]),
        );
        fillStatGroup('pv-tagged-block', 'pv-stats', groups.get('tagged'), 'pv-tagged-note');
        fillStatGroup('pv-ball-block', 'pv-ball-stats', groups.get('ball'), 'pv-ball-note');
        fillStatGroup('pv-running-block', 'pv-running-stats', groups.get('running'), 'pv-running-note');
        fillStatGroup('pv-defending-block', 'pv-defending-stats', groups.get('defending'), 'pv-defending-note');

        renderPlayerChart(reports);
        renderPlayerForm(reports);
        renderPlayerMatches(reports);
        // A coach who was reading one student's running and opens the next
        // should land where every player opens.
        state.openPlayer = { player, totals };
        playerRail?.reset();
        renderPlayerRail();
        show('view-player');
        window.scrollTo(0, 0);
    } catch (err) {
        toast(err.message || 'Could not load that player.', true);
        show('view-main');
    }
}

/**
 * The rail beside one player's season, and the figures that stand above it.
 *
 * The facts read `state.openPlayer` rather than the arguments, and that is not
 * a detail: `mountRail` runs once and keeps the callback, so a closure over the
 * parameters would have pinned the rail to whichever student was opened first
 * and shown their minutes over everyone after them.
 */
let playerRail = null;
function renderPlayerRail() {
    playerRail ||= mountRail({
        body: byId('pv-body'),
        rail: byId('pv-rail'),
        heading: 'This season',
        facts: () => {
            const totals = state.openPlayer?.totals;
            if (!totals) return [];

            // Four facts, the same four the player's own rail carries. There
            // was a fifth for an afternoon — whether this student has signed in
            // — and it went: the pill beside their name says exactly that,
            // three inches away, and the point of these two rails is that a
            // coach and a player read one arrangement rather than two.
            return [
                { label: 'Matches', value: totals.matches || null, tone: 'is-big' },
                {
                    label: 'Minutes',
                    value: totals.minutes || null,
                    note: totals.minutesUnknown
                        ? `${totals.minutesUnknown} without a clock`
                        : null,
                },
                {
                    label: 'Goals + assists',
                    value: totals.goals + totals.assists,
                    tone: totals.goals + totals.assists ? 'is-good' : '',
                },
                {
                    label: 'Filmed',
                    value: totals.cvMatches
                        ? `${totals.cvMatches} of ${totals.matches}`
                        : 'none yet',
                },
            ];
        },
    });
    playerRail.render();
}

/** One player's minutes across the season, same chart the player sees. */
function renderPlayerChart(reports) {
    const host = byId('pv-chart');
    host.innerHTML = '';

    if (!reports.length) {
        host.innerHTML = '<div class="empty">No published matches yet.</div>';
        setText('pv-chart-note', '');
        return;
    }

    host.append(minutesChart(reports));

    const involved = reports.filter((r) => (r.goals || 0) + (r.assists || 0)).length;
    const line = `The line is a full ${FULL_MATCH_MIN}-minute match, oldest match on the left.`;
    setText('pv-chart-note', involved
        ? `${line} Highlighted bars are `
          + `matches they scored or assisted in — ${plural(involved, 'match', 'matches')}.`
        : line);
}

/**
 * The video-derived figures as rates across the season.
 *
 * Same module and same arithmetic as the player's own page, deliberately. This
 * is the one number a coach and a player look at together, and the conversation
 * goes badly if the two screens worked it out differently.
 */
const PV_FORM_GROUPS = [
    { host: 'pv-ball-form', keys: ['touchesPerMin', 'passAccuracy'] },
    { host: 'pv-running-form', keys: ['distancePerMin', 'topSpeed'] },
];

function renderPlayerForm(reports) {
    const forms = seasonForms(reports);
    const byKey = new Map(forms.map((form) => [form.key, form]));

    // Each trace under the total it is the shape of, rather than four unrelated
    // instruments in one panel — the same split the player's own page makes.
    let anyDrawn = false;
    for (const group of PV_FORM_GROUPS) {
        const host = byId(group.host);
        if (!host) continue;
        const mine = group.keys.map((key) => byKey.get(key)).filter(Boolean);
        anyDrawn = Boolean(renderForms(host, mine, { minPoints: MIN_FORM_POINTS }))
            || anyDrawn;
    }

    const block = byId('pv-form-block');
    if (!block) return;
    block.classList.toggle('hidden', !anyDrawn);
    if (!anyDrawn) return;

    setText('pv-form-note', formNote(forms, { measured: 'were filmed' }));
    // The same strip the player sees on their own page. A coach asking why
    // somebody's running figures look thin should be looking at the same
    // answer the player is.
    const strip = byId('pv-coverage');
    if (strip) {
        strip.innerHTML = '';
        strip.append(coverageStrip(reports, { thinBelow: MIN_POINT_MINUTES }));
    }
}

function renderPlayerMatches(reports) {
    const list = byId('pv-matches');
    list.innerHTML = '';

    if (!reports.length) {
        list.innerHTML =
            '<div class="empty">Nothing published for this player yet.<br>'
            + '<span class="muted">Open a match and publish its reports.</span></div>';
        return;
    }

    for (const report of reports) {
        list.append(matchReportRow(report));
    }
}

/** One finished match on a player's season: opponent, date, and their numbers. */
function matchReportRow(report) {
    const row = document.createElement('div');
    row.className = 'list-item pv-row';
    row.innerHTML = `
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>
        <div class="figures"></div>`;

    row.querySelector('.title').textContent = `vs ${report.opponentName || '—'}`;

    const sub = row.querySelector('.sub');
    sub.textContent = report.matchDate || '';
    for (const chip of cardChips(report.yellowCards, report.redCards, CARD_COLOURS)) {
        chip.style.marginLeft = '6px';
        sub.append(chip);
    }

    row.querySelector('.figures').append(
        figure(knownMinutes(report) ? (report.minutesPlayed ?? 0) : '—', 'min'),
        figure(report.goals ?? 0, 'goals'),
        figure(report.assists ?? 0, 'assists'),
    );

    return row;
}
