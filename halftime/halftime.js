// The half-time report.
//
// The design test for everything on this page is: could the coach have seen
// this with their own eyes? They watched the half. They know the score, they
// know it felt scrappy, they saw the goals. So the scoreline is a reference
// point rather than the headline, and the page leads with the two things nobody
// can hold in their head — who is a booking away from a red, and who has run
// the whole half.
//
// It has to be readable standing up, on a phone, in three minutes, by someone
// who is about to talk to fifteen teenagers.

import { onUser, resolveAccess, configWarning } from '../assets/auth.js?v=10';
import {
    getMatch, listMatchRoster, listLog, aggregateMatch,
} from '../assets/db.js?v=10';
import { describeEvent, timelineTone, CARD_COLOURS } from '../assets/events.js?v=10';
import {
    byId, setText, toast, showOnly, clockText, timelineRow, plural, cardChips,
} from '../assets/ui.js?v=10';

const VIEWS = ['view-error', 'view-report'];

// A player who has run this long is a substitution candidate. Not a rule, just
// the threshold that makes the list worth reading rather than listing everyone.
const LONG_SHIFT_MIN = 35;

const params = new URLSearchParams(location.search);
const teamId = params.get('team');
const matchId = params.get('match');

const state = { team: null, match: null, log: [], roster: [], stats: null };

function fail(message) {
    setText('error-msg', message);
    showOnly('view-error', VIEWS);
}

// ---------------------------------------------------------------- decisions

/**
 * The only section a coach has to read. Everything here is something they might
 * act on in the next three minutes, and nothing here is something they could
 * have counted from the touchline.
 */
function renderDecisions() {
    const list = byId('decisions');
    list.innerHTML = '';

    const onField = state.stats.players.filter((p) => p.isActive);

    // A second yellow ends their match, so this is the highest-stakes thing a
    // coach can forget — and it is genuinely easy to forget, because the card
    // was shown twenty minutes ago to someone still running around.
    const booked = onField.filter((p) => p.yellowCards === 1 && p.redCards === 0);
    for (const player of booked) {
        const when = cardMinute(player.id);
        list.append(decision('warn',
            `${player.playerName} is on a yellow`,
            `Booked${when ? ` on ${when}` : ''}. Another one and you finish with ten.`,
            player));
    }

    const sentOff = state.stats.players.filter((p) => p.redCards > 0);
    for (const player of sentOff) {
        list.append(decision('bad',
            `${player.playerName} has been sent off`,
            'You are a player down for the rest of the match.',
            player));
    }

    const bench = state.stats.players.filter(
        (p) => !p.isActive && !(p.stints || []).length
    );

    // Minutes are the other thing that cannot be counted by eye, especially
    // once substitutions and re-entry are in play.
    //
    // At half-time most of the team is tied on the same number, so naming three
    // of them would be an arbitrary pick out of a tie and would read as though
    // those three had done more than the rest. Anyone tied at the top is
    // reported as a group; only a genuinely short list gets named.
    const longestShift = Math.max(0, ...onField.map((p) => p.minutesPlayed));
    const atTop = onField.filter((p) => p.minutesPlayed === longestShift);

    if (longestShift >= LONG_SHIFT_MIN) {
        const restNote = bench.length
            ? ` ${plural(bench.length, 'substitute')} still unused.`
            : ' No unused substitutes left.';

        list.append(atTop.length > 3
            ? decision('info',
                `${atTop.length} players have been on the whole time`,
                `${longestShift}′ each, all still on the field.${restNote}`)
            : decision('info',
                atTop.length === 1 ? 'Longest shift' : 'Longest shifts',
                `${atTop.map((p) => p.playerName).join(', ')} — ${longestShift}′, `
                + `still on.${restNote}`));
    } else if (bench.length) {
        list.append(decision('info',
            `${plural(bench.length, 'substitute')} unused`,
            bench.map((p) => p.playerName).join(', ')));
    }

    if (!list.children.length) {
        list.innerHTML =
            '<div class="empty">Nothing needs a decision — no cards, and nobody '
            + 'has been on long enough to need a rest.</div>';
    }
}

function decision(tone, title, detail, player) {
    const row = document.createElement('div');
    row.className = `decision ${tone}`;
    row.innerHTML = `
        <span class="jersey"></span>
        <div class="grow">
            <div class="d-title"></div>
            <div class="d-detail"></div>
        </div>`;

    const jersey = row.querySelector('.jersey');
    if (player) jersey.textContent = player.jerseyNumber ?? '—';
    else jersey.remove();

    row.querySelector('.d-title').textContent = title;
    row.querySelector('.d-detail').textContent = detail;
    return row;
}

/** When a player's card was shown, as a match minute. */
function cardMinute(playerId) {
    const card = state.log.find(
        (e) => e.kind === 'event' && e.type === 'card' && e.playerId === playerId
    );
    return card ? `${Math.floor(card.matchClockS / 60)}′` : null;
}

// ---------------------------------------------------------------- tallies

/**
 * The counts a coach feels but does not track. "We're getting battered" is a
 * feeling; seven corners to one is a fact you can say out loud to a team.
 *
 * Throw-ins, goal kicks and out-of-bounds are tagged but deliberately left out
 * — they are the noise of the match, not a read on it.
 */
function renderTallies() {
    const list = byId('tallies');
    list.innerHTML = '';

    const { us, them } = state.stats.counts;

    const rows = [
        ['Corners', us.corner, them.corner, 'high'],
        ['Free kicks won', us.free_kick, them.free_kick, 'high'],
        ['Fouls committed', us.foul, them.foul, 'low'],
        ['Offside', us.offside, them.offside, 'low'],
        ['Cards', us.card, them.card, 'low'],
    ];

    for (const [label, ours, theirs, better] of rows) {
        if (!ours && !theirs) continue;
        list.append(tally(label, ours, theirs, better));
    }

    if (!list.children.length) {
        list.innerHTML = '<div class="empty">Nothing tagged yet beyond the restarts.</div>';
    }

    const total = Object.values(us).reduce((a, b) => a + b, 0)
        + Object.values(them).reduce((a, b) => a + b, 0);
    setText('numbers-note',
        `From ${plural(total, 'tagged event')}. Fouls and cards are counted `
        + 'against whoever committed them.');
}

/**
 * One paired row. The bar is proportional so the gap is visible before the
 * numbers are read — at a glance, from arm's length.
 */
function tally(label, ours = 0, theirs = 0, better = 'high') {
    const row = document.createElement('div');
    row.className = 'tally';
    row.innerHTML = `
        <span class="t-us num"></span>
        <div class="t-bar"><div class="t-fill us"></div><div class="t-fill them"></div></div>
        <span class="t-them num"></span>
        <div class="t-label"></div>`;

    row.querySelector('.t-us').textContent = ours;
    row.querySelector('.t-them').textContent = theirs;
    row.querySelector('.t-label').textContent = label;

    const total = ours + theirs;
    const share = total ? (ours / total) * 100 : 50;
    row.querySelector('.t-fill.us').style.width = `${share}%`;
    row.querySelector('.t-fill.them').style.width = `${100 - share}%`;

    // Colour the side that is ahead on a count where being ahead is good, and
    // the side that is ahead on a count where it is not.
    if (total && ours !== theirs) {
        const weLead = ours > theirs;
        const goodForUs = better === 'high' ? weLead : !weLead;
        row.classList.add(goodForUs ? 'ours-good' : 'ours-bad');
    }

    return row;
}

// ---------------------------------------------------------------- minutes

function renderMinutes() {
    const list = byId('minutes');
    list.innerHTML = '';

    const played = state.stats.players
        .filter((p) => (p.stints || []).length)
        .sort((a, b) => b.minutesPlayed - a.minutesPlayed);

    if (!played.length) {
        list.innerHTML = '<div class="empty">No lineup was saved for this match.</div>';
        return;
    }

    const most = played[0].minutesPlayed || 1;

    for (const player of played) {
        const row = document.createElement('div');
        row.className = `minute-row ${player.isActive ? 'on' : 'off'}`;
        row.innerHTML = `
            <span class="jersey"></span>
            <span class="m-name"></span>
            <div class="m-bar"><div class="m-fill"></div></div>
            <span class="m-value num"></span>
            <span class="m-cards"></span>`;

        row.querySelector('.jersey').textContent = player.jerseyNumber ?? '—';
        row.querySelector('.m-name').textContent = player.playerName;
        row.querySelector('.m-fill').style.width =
            `${Math.round((player.minutesPlayed / most) * 100)}%`;
        row.querySelector('.m-value').textContent = `${player.minutesPlayed}′`;
        row.querySelector('.m-cards').append(
            ...cardChips(player.yellowCards, player.redCards, CARD_COLOURS)
        );

        list.append(row);
    }

    const on = played.filter((p) => p.isActive).length;
    setText('bench-note',
        `${on} on the field. Dimmed rows have come off — their minutes stopped `
        + 'when they did.');
}

// ---------------------------------------------------------------- timeline

function renderTimeline() {
    const list = byId('timeline');
    list.innerHTML = '';

    if (!state.log.length) {
        list.innerHTML = '<div class="empty">Nothing tagged yet.</div>';
        return;
    }

    const nameById = new Map(state.roster.map((r) => [r.id, r.playerName]));
    const usName = state.team.name || 'Us';
    const themName = state.match.opponentName || 'Them';

    for (const entry of state.log.slice().reverse()) {
        list.append(timelineRow({
            clock: clockText(entry.matchClockS),
            text: describeEvent(entry, {
                usName, themName, playerName: nameById.get(entry.playerId),
            }),
            sideLabel: entry.kind === 'period'
                ? ''
                : (entry.side === 'them' ? themName : usName),
            tone: timelineTone(entry),
        }));
    }
}

// ---------------------------------------------------------------- load

const PERIOD_HEADINGS = {
    scheduled: 'Not started',
    first_half: 'First half so far',
    halftime: 'Half-time',
    second_half: 'Second half so far',
    full_time: 'Full time',
};

async function load() {
    const [match, roster, log] = await Promise.all([
        getMatch(teamId, matchId),
        listMatchRoster(teamId, matchId),
        listLog(teamId, matchId),
    ]);

    if (!match) return fail('That match no longer exists.');

    state.match = match;
    state.roster = roster;
    state.log = log;
    state.stats = aggregateMatch(log, roster);

    setText('ht-period', PERIOD_HEADINGS[match.status] || 'So far');
    setText('ht-us', state.team.name || 'Us');
    setText('ht-them', match.opponentName || 'Them');
    setText('ht-score-us', state.stats.counts.us.goal ?? 0);
    setText('ht-score-them', state.stats.counts.them.goal ?? 0);
    setText('ht-clock', log.length
        ? `${clockText(state.stats.matchEndS)} played`
        : 'Nothing tagged yet');

    byId('link-dashboard').href = `../coach/?team=${encodeURIComponent(teamId)}`;

    renderDecisions();
    renderTallies();
    renderMinutes();
    renderTimeline();
    showOnly('view-report', VIEWS);
}

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    byId('btn-refresh').addEventListener('click', () => {
        byId('loading').classList.remove('hidden');
        load()
            .then(() => toast('Updated'))
            .catch((err) => fail(err.message || 'Could not read that match.'));
    });

    onUser(async (user) => {
        if (!user) { location.href = '../'; return; }
        if (!teamId || !matchId) {
            return fail('This link is missing the team or match. Open the match '
                + 'from your dashboard instead.');
        }

        try {
            const access = await resolveAccess(user);
            state.team = access.teams.find((t) => t.id === teamId);
            if (!state.team) {
                return fail('This account does not coach that squad.');
            }
            await load();
        } catch (err) {
            fail(err.message || 'Could not read that match.');
        }
    });
}

init();
