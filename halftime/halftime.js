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

import { onUser, resolveAccess, configWarning } from '../assets/auth.js?v=90';
import {
    getMatch, listMatchRoster, listLog, aggregateMatch,
    readCvStats, cvConfidence,
} from '../assets/db.js?v=90';
import { describeEvent, timelineTone, CARD_COLOURS } from '../assets/events.js?v=90';
import {
    possessionIsInPlay, cvReads, xgTrust, groupStats, clockFromMatch,
    taggedTeamRows, taggedCount,
    SHARE, COUNT, RATE,
} from '../assets/report.js?v=90';
import { sampleCvSummary, SAMPLE_NOTICE } from '../assets/sample-report.js?v=90';
import { renderMatchVideo, teamMarks } from '../assets/match-video.js?v=90';
import {
    byId, setText, toast, showOnly, clockText, timelineRow, plural, cardChips,
    tally, groupHead,
} from '../assets/ui.js?v=90';

const VIEWS = ['view-error', 'view-report'];

// A player who has run this long is a substitution candidate. Not a rule, just
// the threshold that makes the list worth reading rather than listing everyone.
const LONG_SHIFT_MIN = 35;

const params = new URLSearchParams(location.search);
const teamId = params.get('team');
const matchId = params.get('match');

const state = {
    team: null, match: null, log: [], roster: [], stats: null, cv: null,
    // Whether the video-derived rows are being previewed with invented numbers.
    // Nothing on this page writes, so the preview reaches every one of them.
    cvPreview: false,
};

/** The video-derived document the page should render — real, or the sample. */
const activeCv = () => (state.cvPreview ? sampleCvSummary() : state.cv);

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
    // A half-time log always has entries in it, so these are always numbers in
    // practice — but the whole point of the change that made them nullable is
    // that "in practice" was what produced a squad of nought-minute starters.
    const shift = (p) => p.minutesPlayed ?? 0;
    const longestShift = Math.max(0, ...onField.map(shift));
    const atTop = onField.filter((p) => shift(p) === longestShift);

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

    // What the video saw, as sentences. Last in this block on purpose: a
    // second yellow is a decision to make right now, and a read on the shape is
    // something to talk about — the ordering says which is which without
    // needing a heading to say so.
    //
    // Marked `est` rather than `info` so an estimate never sits in the same
    // visual register as a card somebody was actually shown.
    for (const read of cvReads(activeCv(), {
        videoOffsetS: state.match?.videoOffsetS ?? 0,
    })) {
        list.append(decision('est', read.title, read.detail));
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

    // Video-derived rows sit in the same groups as the tapped ones, each
    // carrying a confidence mark. Same groups because a coach wants one picture
    // of the half; marked because an estimated pass count and a tapped corner
    // count are not the same kind of fact, and nothing else on the row says so.
    //
    // Grouped by the same list the coach's full report uses. Thirteen bars in
    // one column is a wall on a phone, and the headings are what let somebody
    // standing in a changing room find the two they came for. They are also the
    // one thing keeping this page and the match view agreeing about what counts
    // as attacking rather than passing.
    for (const group of groupStats([...taggedTallies(), ...cvTallies()])) {
        list.append(groupHead(group.title));
        for (const row of group.rows) {
            list.append(tally(
                row.label, row.usN, row.themN, row.better, row.confidence,
                { kind: row.kind },
            ));
        }
    }

    if (!list.children.length) {
        list.innerHTML = '<div class="empty">Nothing tagged yet beyond the restarts.</div>';
    }

    // Read here rather than closed over. These used to be destructured at the
    // top of this function; the group-stats refactor moved that line down into
    // `taggedTallies` and left this behind, so every half-time load threw a
    // ReferenceError and the page said "can't open that match".
    const { us, them } = state.stats.counts;
    const total = Object.values(us).reduce((a, b) => a + b, 0)
        + Object.values(them).reduce((a, b) => a + b, 0);
    setText('numbers-note',
        `From ${plural(total, 'tagged event')}. Fouls and cards are counted `
        + 'against whoever committed them.'
        + (activeCv() ? sampleOrMeasuredNote() : '')
        // One clause, not the coach page's full quality line. This page is read
        // standing up in three minutes, and the only caveat that changes how the
        // possession row above is read is what its denominator was.
        + (cvLiveNote() || ''));
}

/**
 * The counts somebody tapped, as typed rows for `groupStats`.
 *
 * These are `COUNT` rows, which `comparePair` draws as splits — and the comment
 * that stood here said the opposite ("none is drawn as a split"), citing the
 * function that contradicts it. Five corners to one and fifty to ten really are
 * not the same half, and the answer is not to refuse the split: it is
 * `tentative`, which draws a lead smaller than chance would hand out as a
 * hollow bar. 5–1 comes out hollow, 50–10 solid.
 *
 * Two differences from the coach's full report, both deliberate and both about
 * who is reading. **Goals are left out** — the scoreline is the biggest thing
 * on this page already, and the rule here is not to report what the coach stood
 * and watched. **A row neither side registered is dropped**, because this is
 * read standing up in three minutes; in the report it stays, because "we
 * conceded no corners" is worth being able to look up.
 */
function taggedTallies() {
    return taggedTeamRows(state.stats.counts, {
        subs: null, goals: false, dropEmpty: true,
    });
}

/** The rows that came from footage rather than from somebody's thumb. */
function cvTallies() {
    const cv = activeCv();
    if (!cv) return [];

    const ours = cv.teams?.team_a;
    const theirs = cv.teams?.team_b;
    if (!ours || !theirs) return [];

    const quality = cv.quality || {};
    const events = cvConfidence(quality, 'events');
    const possession = cvConfidence(quality, 'possession');

    const rows = [
        // "in play" only when a tagged log told the pipeline when it wasn't.
        // Without one this is still possession of every second including the
        // ones spent waiting for a throw-in, and the label must not upgrade it.
        // The only true split on the page: their possession IS the rest of
        // ours, so the boundary between the two is the whole story and the bar
        // is always full. Nothing else here works that way.
        ['possession',
            possessionIsInPlay(quality) ? 'Possession in play %' : 'Possession %',
            pct(ours.possession_pct), pct(theirs.possession_pct),
            'high', possession, SHARE],
        ['passing', 'Passes completed', ours.passes_completed, theirs.passes_completed,
            'high', events],
        // Percentages of two different denominators, so each runs on its own
        // 0-100 scale. As a split these would come out near even whatever the
        // gap between them.
        ['passing', 'Pass accuracy %', pct(ours.pass_accuracy), pct(theirs.pass_accuracy),
            'high', events, RATE],
        ['defending', 'Tackles', ours.tackles, theirs.tackles, 'high', events],
        ['defending', 'Interceptions', ours.interceptions, theirs.interceptions,
            'high', events],
        ['defending', 'Recoveries', ours.recoveries, theirs.recoveries, 'high', events],
        ['attacking', 'Shots', ours.shots, theirs.shots, 'high', events],
        ['attacking', 'Shots on target', ours.shots_on_target, theirs.shots_on_target,
            'high', events],
        // The catalog's headline number, and until 2026-08-02 the pipeline never
        // computed it — see cv/xg_bridge.py. One decimal place, because two
        // would claim a precision the noise measurement says is not there.
        ['attacking', xgLabel(), xg(ours.xg), xg(theirs.xg), 'high', events],
        ['attacking', 'Entries into the final third', ours.final_third_entries,
            theirs.final_third_entries, 'high', events],
    ];

    return rows
        // A null means the pipeline could not measure it — usually for want of
        // a calibration. Showing a zero would say it measured none.
        .filter(([, , a, b]) => (a || b) && a != null && b != null)
        // A count unless the row says otherwise, because most of them are. The
        // two that are not — possession and pass accuracy — say so above.
        .map(([type, label, usN, themN, better, confidence, kind = COUNT]) =>
            ({ type, label, usN, themN, better, confidence, kind, value: usN }));
}

const pct = (share) => (share == null ? null : Math.round(share * 100));

// One decimal place. Two would claim a precision the model does not have: half
// a metre of position error moves a single shot's xG by about 0.035, so the
// second decimal is noise wearing a number's clothes. See tests/test_xg_noise.py.
//
// Null at a calibration too loose to support even a total, which drops the row
// rather than printing a figure with an error bar wider than itself. See
// `xgTrust`; the band is shared with the coach's page so a coach cannot be shown
// a number at half time that the full report then withholds.
const xg = (value) => {
    if (value == null || xgTrust(activeCv()?.calibrationErrorM) === 'none') return null;
    return Number(value.toFixed(1));
};

// Named for what it is rather than as "xG", which means nothing to most of the
// people who will read this page standing on a touchline.
const xgLabel = () => 'Chances created (xG)';

/**
 * What the dotted rows are — measured, or invented.
 *
 * The dotted rows sit in the same list as corners somebody tapped, and the only
 * thing distinguishing them is this sentence and a confidence mark. On the
 * preview that sentence has to change, or the page claims a video it never had.
 */
const sampleOrMeasuredNote = () => (state.cvPreview
    ? ` ${SAMPLE_NOTICE}`
    : ' Dotted rows were measured from the video.');

/** The one caveat that changes how the possession row is read, or nothing. */
function cvLiveNote() {
    // Only worth saying when a possession row was actually drawn. `cvTallies`
    // drops it when the pipeline could not measure one, and a caveat about a
    // figure nobody can see is just another sentence in the way.
    const cv = activeCv();
    if (cv?.teams?.team_a?.possession_pct == null) return '';
    const quality = cv.quality || {};
    if (!possessionIsInPlay(quality)) {
        return ' Possession counts stoppages as play — no tagged log reached'
            + ' the video run.';
    }
    return ` The tagged log put ${Math.round(quality.live_share * 100)}% of the`
        + ' half in play, and possession is measured over that.';
}

// ---------------------------------------------------------------- minutes

function renderMinutes() {
    const list = byId('minutes');
    list.innerHTML = '';

    const played = state.stats.players
        .filter((p) => (p.stints || []).length)
        .sort((a, b) => (b.minutesPlayed ?? 0) - (a.minutesPlayed ?? 0));

    if (!played.length) {
        list.innerHTML = '<div class="empty">No lineup was saved for this match.</div>';
        return;
    }

    const most = played[0].minutesPlayed || 1;
    const known = (p) => p.minutesPlayed != null;

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
        row.querySelector('.m-fill').style.width = known(player)
            ? `${Math.round((player.minutesPlayed / most) * 100)}%` : '0%';
        row.querySelector('.m-value').textContent = known(player)
            ? `${player.minutesPlayed}′` : '—';
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

// The timeline is the one section with no natural length — a busy match can run
// to eighty entries. Everything above it is a fixed size, so left uncapped it
// alone decides how much scrolling a coach does in the three minutes they have.
const TIMELINE_PREVIEW = 8;

let timelineExpanded = false;

function renderTimeline() {
    const list = byId('timeline');
    const more = byId('btn-more-timeline');
    list.innerHTML = '';

    if (!state.log.length) {
        list.innerHTML = '<div class="empty">Nothing tagged yet.</div>';
        more.classList.add('hidden');
        return;
    }

    const nameById = new Map(state.roster.map((r) => [r.id, r.playerName]));
    const usName = state.team.name || 'Us';
    const themName = state.match.opponentName || 'Them';

    const newestFirst = state.log.slice().reverse();
    const shown = timelineExpanded
        ? newestFirst
        : newestFirst.slice(0, TIMELINE_PREVIEW);

    for (const entry of shown) {
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

    const hidden = newestFirst.length - shown.length;
    more.classList.toggle('hidden', !hidden && !timelineExpanded);
    more.textContent = timelineExpanded
        ? 'Show less'
        : `Show all ${newestFirst.length}`;
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
    const [match, roster, log, cv] = await Promise.all([
        getMatch(teamId, matchId),
        listMatchRoster(teamId, matchId),
        listLog(teamId, matchId),
        // Never run for most matches, so a failure here must not take the page
        // down with it — the tagged half-time report is the thing the coach is
        // standing in a changing room waiting for.
        readCvStats(teamId, matchId).catch(() => null),
    ]);

    if (!match) return fail('That match no longer exists.');

    state.match = match;
    state.roster = roster;
    state.log = log;
    state.cv = cv;
    // Off on every load, including a refresh. Refresh is the button a coach
    // presses when they expect new numbers; coming back to invented ones
    // wearing the same dotted rows is the worst moment for this to be on.
    state.cvPreview = false;
    state.stats = aggregateMatch(log, roster);

    setText('ht-period', PERIOD_HEADINGS[match.status] || 'So far');
    setText('ht-us', state.team.name || 'Us');
    setText('ht-them', match.opponentName || 'Them');
    // The clock beneath already distinguishes "nothing tagged yet" from a
    // match at 00:00; until now the scoreline above it did not. See
    // `taggedCount`.
    setText('ht-score-us', taggedCount(state.stats.counts.us.goal, log));
    setText('ht-score-them', taggedCount(state.stats.counts.them.goal, log));
    setText('ht-clock', log.length
        ? `${clockText(state.stats.matchEndS)} played`
        : 'Nothing tagged yet');

    byId('link-dashboard').href = `../coach/?team=${encodeURIComponent(teamId)}`;

    renderDecisions();
    renderTallies();
    renderMinutes();
    renderTimeline();
    renderVideo();
    renderSampleToggle();
    showOnly('view-report', VIEWS);
}

/** Offered only when this match has no run of its own. */
function renderSampleToggle() {
    const row = byId('ht-sample-toggle');
    if (!row) return;

    const offerable = !state.cv;
    row.classList.toggle('hidden', !offerable);
    row.classList.toggle('is-on', state.cvPreview);
    if (!offerable) return;

    byId('btn-ht-sample').textContent = state.cvPreview
        ? 'Hide the sample'
        : 'Preview with sample data';
    setText('ht-sample-hint', state.cvPreview
        ? SAMPLE_NOTICE
        : 'See the rows a filmed match adds, using made-up numbers.');
}

function toggleSample() {
    state.cvPreview = !state.cvPreview;
    // Both, not just the tallies: cvReads writes the plain-language rows into
    // the decisions block, and leaving those stale would put a read about the
    // sample's shape next to a page that had stopped showing the sample.
    renderDecisions();
    renderTallies();
    renderSampleToggle();
}

// ---------------------------------------------------------------- the video
//
// Usually nothing. At half-time the footage is still on somebody's phone at the
// side of the pitch, and this page is being read four minutes after the whistle.
// It earns its place on the second visit — the walk-through the next day opens
// the same URL, and the goals are already marked on the strip.
//
// Hidden without a link, rather than shown empty. Every moment it would mark is
// already listed under "How it went" a few inches above, so an empty video block
// would be the same information twice with the interesting half missing.

let video = null;

function renderVideo() {
    const block = byId('match-video-block');
    const url = state.match?.videoUrl;

    video?.destroy?.();
    video = null;

    if (!url) {
        block.classList.add('hidden');
        return;
    }
    block.classList.remove('hidden');

    const nameById = new Map(state.roster.map((r) => [r.id, r.playerName]));
    const usName = state.team.name || 'Us';
    const themName = state.match.opponentName || 'Them';

    const marks = teamMarks(state.log, (entry) => describeEvent(entry, {
        usName, themName, playerName: nameById.get(entry.playerId),
    }));

    video = renderMatchVideo(
        {
            video: byId('match-video'),
            strip: byId('match-scrubber'),
            list: byId('match-moments'),
            note: byId('match-video-note'),
        },
        {
            url,
            clock: clockFromMatch(state.match),
            marks,
            clockText,
            extraTimes: state.log.map((e) => e.matchClockS || 0),
            emptyText: 'No goals, cards or substitutions tagged yet.',
            notes: {
                embed: `${plural(marks.length, 'moment')} marked. Tap one to jump `
                    + 'straight to it.',
                link: 'That link cannot be played inside PitchIQ, so the times '
                    + 'below are match-clock readings rather than buttons.',
                none: '',
            },
        },
    );
}

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    byId('btn-more-timeline').addEventListener('click', () => {
        timelineExpanded = !timelineExpanded;
        renderTimeline();
    });

    byId('btn-ht-sample').addEventListener('click', toggleSample);

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
