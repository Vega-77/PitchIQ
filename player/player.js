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

import { onUser, signOut, configWarning } from '../assets/auth.js?v=18';
import { myReports, seasonTotals, cvPlayerConfidence } from '../assets/db.js?v=18';
import { CARD_COLOURS } from '../assets/events.js?v=18';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=18';
import { mount as mountVideo, videoKind, videoTime } from '../assets/video.js?v=18';
import {
    byId, setText, toast, showOnly, clockText, statCard, figure, cardChips,
    plural, minutesChart, tally,
} from '../assets/ui.js?v=18';

const VIEWS = ['view-empty', 'view-reports', 'view-match'];

// The match currently open, and its video handle, so a moment can seek it.
const open = { report: null, video: null };

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

    grid.append(...videoCards(totals));
}

/**
 * The numbers that come from footage rather than from the coach's tablet.
 *
 * Marked, every one of them. A player comparing themselves to a teammate
 * deserves to know which figures were watched by a person and which were
 * worked out by a machine from a video where the ball is visible about two
 * thirds of the time.
 *
 * Only shown for filmed matches, and averaged over those rather than over the
 * season — dividing by matches nobody filmed would quietly halve everything.
 */
function videoCards(totals) {
    if (!totals.cvMatches) return [];

    const cards = [
        statCard(totals.cvTouches, 'Touches', 'is-muted', 'medium'),
        statCard(totals.cvTackles, 'Tackles won', 'is-muted', 'medium'),
    ];

    if (totals.cvPassesAttempted) {
        const accuracy = Math.round(
            (totals.cvPassesCompleted / totals.cvPassesAttempted) * 100,
        );
        cards.push(statCard(`${accuracy}%`, 'Pass accuracy', 'is-muted', 'medium'));
    }
    if (totals.cvDistanceM) {
        cards.push(statCard(
            (totals.cvDistanceM / 1000).toFixed(1), 'km covered', 'is-muted', 'medium',
        ));
    }
    if (totals.cvTopSpeedKmh) {
        cards.push(statCard(
            totals.cvTopSpeedKmh.toFixed(1), 'Top speed km/h', 'is-muted', 'medium',
        ));
    }
    if (totals.cvSprintCount) {
        cards.push(statCard(totals.cvSprintCount, 'Sprints', 'is-muted', 'medium'));
    }

    return cards;
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
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'list-item match-row';
        row.addEventListener('click', () => openMatch(report));
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

// ---------------------------------------------------------------- one match

/**
 * One match, from this player's side of it.
 *
 * Everything here was written into their own report when the coach published,
 * so opening it costs no reads and can expose nothing the season list could
 * not already. Reports published before this existed have no timeline and no
 * score; the view degrades to what is there rather than erroring.
 */
function openMatch(report) {
    open.video?.destroy();
    open.video = null;
    open.report = report;

    setText('md-date', report.matchDate || '');
    setText('md-title', `vs ${report.opponentName || '—'}`);
    setText('md-score-us', report.scoreUs ?? '—');
    setText('md-score-them', report.scoreThem ?? '—');
    setText('md-line', matchLine(report));

    renderMatchStats(report);
    renderVideo(report);
    renderTeam(report);

    showOnly('view-match', VIEWS);
    window.scrollTo(0, 0);
}

/** One sentence about their afternoon, so the page opens by saying something. */
function matchLine(report) {
    const bits = [];
    if (report.goals) bits.push(plural(report.goals, 'goal'));
    if (report.assists) bits.push(plural(report.assists, 'assist'));

    const minutes = report.minutesPlayed ?? 0;
    const played = minutes ? `${minutes} minutes` : 'an unused substitute';

    if (report.scoreUs == null) return bits.length ? bits.join(' and ') : played;

    const result = report.scoreUs > report.scoreThem ? 'Won'
        : report.scoreUs < report.scoreThem ? 'Lost' : 'Drew';
    return bits.length
        ? `${result}. You played ${played} and got ${bits.join(' and ')}.`
        : `${result}. You played ${played}.`;
}

function renderMatchStats(report) {
    const grid = byId('md-stats');
    grid.innerHTML = '';
    grid.append(
        statCard(report.minutesPlayed ?? 0, 'Minutes'),
        statCard(report.goals ?? 0, 'Goals', report.goals ? 'is-good' : 'is-muted'),
        statCard(report.assists ?? 0, 'Assists', report.assists ? 'is-good' : 'is-muted'),
        statCard(report.fouls ?? 0, 'Fouls', 'is-muted'),
    );

    const cards = (report.yellowCards ?? 0) + (report.redCards ?? 0);
    if (cards) grid.append(statCard(cards, 'Cards', 'is-warn'));

    // Video-derived, and marked as such. The confidence comes from how many
    // tracked fragments this player had to be assembled from — every join is
    // a place the tracker could have picked up somebody else.
    const trust = cvPlayerConfidence(report.cvClusterCount);
    if (report.cvTouches != null) {
        grid.append(statCard(report.cvTouches, 'Touches', 'is-muted', trust));
    }
    if (report.cvDistanceM != null) {
        grid.append(statCard(
            (report.cvDistanceM / 1000).toFixed(2), 'km covered', 'is-muted', trust,
        ));
    }
    if (report.cvTopSpeedKmh != null) {
        grid.append(statCard(
            report.cvTopSpeedKmh.toFixed(1), 'Top speed km/h', 'is-muted', trust,
        ));
    }
    if (report.cvTackles != null) {
        grid.append(statCard(report.cvTackles, 'Tackles won', 'is-muted', trust));
    }
}

// ---------------------------------------------------------------- the video

/**
 * The match video with this player's moments marked along it.
 *
 * The marks are the point. A player is not going to scrub a 90-minute video
 * looking for the twelve seconds they were involved in, so every moment is a
 * button that seeks straight to it.
 *
 * Touches from the CV pipeline slot into the same strip once they exist — they
 * are the same shape as a tagged moment, a clock reading and a label. Until
 * then this shows what a human tagged, which is real today.
 */
function renderVideo(report) {
    const block = byId('md-video-block');
    const moments = (report.timeline || []).filter((e) => e.mine);
    // Written by cv/publish.py, and only for a player whose cluster a coach
    // has confirmed — an unconfirmed one would put somebody else's touches on
    // this player's video.
    const touches = report.cvTouchTimes || [];

    // Nothing to show at all: no video and nothing to mark on one.
    if (!report.videoUrl && !moments.length) {
        block.classList.add('hidden');
        return;
    }
    block.classList.remove('hidden');

    const host = byId('md-video');
    const offset = report.videoOffsetS ?? 0;

    if (report.videoUrl && videoKind(report.videoUrl)) {
        open.video = mountVideo(host, report.videoUrl);
        setText('md-video-note', touches.length
            ? `${plural(moments.length, 'tagged moment')} and `
              + `${plural(touches.length, 'touch', 'touches')} found in the video. `
              + 'Tap any of them to jump there.'
            : `${plural(moments.length, 'moment')}. Tap one to jump to it.`);
    } else {
        host.innerHTML = '';
        if (report.videoUrl) {
            // A link we will not embed — a Drive share, say. Still worth giving
            // them, just not as a player we can seek.
            const link = document.createElement('a');
            link.className = 'btn small';
            link.href = report.videoUrl;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = 'Open the match video';
            host.append(link);
        }
        setText('md-video-note', report.videoUrl
            ? 'That video link cannot be played inside PitchIQ, so the times below '
              + 'are match-clock readings.'
            : 'No video for this match yet — ask your coach to add one. The times '
              + 'below are match-clock readings.');
    }

    renderScrubber(report, moments, touches, offset);
    renderMoments(report, moments, touches, offset);
}

/** A strip of the match with a tick at each moment, tappable. */
function renderScrubber(report, moments, touches, offset) {
    const strip = byId('md-scrubber');
    strip.innerHTML = '';

    // The match end, so ticks sit in proportion. Falls back to 90 minutes when
    // nothing later was tagged.
    const end = Math.max(
        90 * 60,
        ...(report.timeline || []).map((e) => e.clockS || 0),
        ...touches,
    ) || 5400;

    const tick = (clockS, className, label) => {
        const mark = document.createElement('button');
        mark.type = 'button';
        mark.className = `tick ${className}`;
        mark.style.left = `${Math.min(100, (clockS / end) * 100)}%`;
        mark.title = `${clockText(clockS)} — ${label}`;
        mark.setAttribute('aria-label', mark.title);
        mark.addEventListener('click', () => seekTo(clockS, offset));
        strip.append(mark);
    };

    for (const t of touches) tick(t, 'is-touch', 'Touch');
    for (const m of moments) tick(m.clockS, `is-${m.type}`, m.label);

    const half = document.createElement('span');
    half.className = 'tick-half';
    half.style.left = `${Math.min(100, ((45 * 60) / end) * 100)}%`;
    strip.append(half);
}

function renderMoments(report, moments, touches, offset) {
    const list = byId('md-moments');
    list.innerHTML = '';

    if (!moments.length && !touches.length) {
        list.innerHTML =
            '<div class="empty">Nothing was tagged for you in this match.</div>';
        return;
    }

    const rows = [
        ...moments.map((m) => ({ clockS: m.clockS, label: m.label, type: m.type })),
        ...touches.map((t) => ({ clockS: t, label: 'Touch', type: 'touch' })),
    ].sort((a, b) => a.clockS - b.clockS);

    for (const row of rows) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `moment is-${row.type}`;
        button.innerHTML = '<span class="m-clock"></span><span class="m-label"></span>';
        button.querySelector('.m-clock').textContent = clockText(row.clockS);
        button.querySelector('.m-label').textContent = row.label;
        button.addEventListener('click', () => seekTo(row.clockS, offset));
        list.append(button);
    }
}

function seekTo(clockS, offset) {
    if (!open.video) {
        toast('No playable video for this match yet.');
        return;
    }
    open.video.seek(videoTime(clockS, offset));
    byId('md-video').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ---------------------------------------------------------------- the team

/**
 * How the team did, without naming anyone.
 *
 * Goalscorers appear on the timeline above because they are announced out loud
 * at the match anyway. Everything else about a teammate — minutes, fouls, and
 * above all cards — stays with the coach.
 */
function renderTeam(report) {
    const list = byId('md-team');
    list.innerHTML = '';

    const counts = report.teamCounts;
    if (!counts) {
        list.innerHTML =
            '<div class="empty">Team numbers were not saved for this match.</div>';
        return;
    }

    const rows = [
        ['Goals', counts.us.goal, counts.them.goal, 'high'],
        ['Corners', counts.us.corner, counts.them.corner, 'high'],
        ['Free kicks won', counts.us.free_kick, counts.them.free_kick, 'high'],
        ['Fouls committed', counts.us.foul, counts.them.foul, 'low'],
        ['Offside', counts.us.offside, counts.them.offside, 'low'],
        ['Cards', counts.us.card, counts.them.card, 'low'],
    ];

    for (const [label, us, them, better] of rows) {
        if (!us && !them) continue;
        list.append(tally(label, us ?? 0, them ?? 0, better));
    }

    if (!list.children.length) {
        list.innerHTML = '<div class="empty">Nothing beyond the restarts was tagged.</div>';
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

    byId('btn-back').addEventListener('click', () => {
        // Tear the player down on the way out, or a YouTube iframe keeps
        // playing behind the season list.
        open.video?.destroy();
        open.video = null;
        showOnly('view-reports', VIEWS);
    });

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
