// A player's season grouped, and the season at a glance.
//
// Part of `report.js`, which re-exports it.

import { num, count } from './report-format.js?v=151';
import { knownMinutes } from './report-clock.js?v=151';
import { positionOf } from './report-positions.js?v=151';

// ------------------------------------------------ a player's season, grouped
//
// The same season is read on three screens — the player's own page, the coach's
// view of that player, and the coach's roster. It was three piles of boxes
// built three times, and they had already drifted: one called it "Tackles" and
// another "Tackles won", one counted interceptions and another did not, and
// only one of them had ever heard of carries.
//
// Worse than the drift is what a flat pile hides. Thirteen boxes in a row is a
// wall a reader scans left to right to find the one thing they came for, and it
// flattens the distinction this project cares most about: a goal somebody
// pressed a button for and a distance a machine estimated end up as the same
// box with a small dot in the corner.
//
// So the grouping is a decision, made once, here, and tested — and it hands
// back specs rather than elements, so `statGroup` in ui.js can draw them and
// this module can stay importable by a test file with no DOM.

/** A full high-school match: two forty-minute halves. The reference line on
 *  the minutes charts is this too, so a full shift reaches it rather than
 *  stopping short of a professional ninety. */
export const FULL_MATCH_MIN = 80;

/**
 * A player's season as titled groups of figures.
 *
 * `reports` is needed as well as `totals` for the two figures that are about
 * particular matches rather than about the sum: their best afternoon, and how
 * many full matches they played. The rest is arithmetic over `seasonTotals`.
 *
 * Groups with nothing in them are dropped, so a season nobody filmed comes back
 * as one group rather than one group and three empty headings.
 */
export function seasonGroups(reports, totals, options = {}) {
    // Whose season this is being read as. A player opens their own page and a
    // coach opens the same season about somebody else, so one of the notes has
    // to change person — and doing that by patching the finished string is how
    // "a pass is two touches with a ball between them" became "…between you".
    const { second = false } = options;
    const them = second ? 'you' : 'them';
    const played = reports || [];
    const t = totals || {};
    const involvements = (r) => (r?.goals || 0) + (r?.assists || 0);

    const tagged = [
        { value: t.matches ?? 0, label: 'Matches' },
        {
            // Absent is not zero: a season made of untagged matches has no
            // minutes total, and printing 0 would say they never played.
            value: t.minutes || '—',
            label: 'Minutes played',
            tone: t.minutesUnknown ? 'is-muted' : '',
        },
        { value: t.goals ?? 0, label: 'Goals', tone: t.goals ? 'is-good' : 'is-muted' },
        { value: t.assists ?? 0, label: 'Assists', tone: t.assists ? 'is-good' : 'is-muted' },
    ];

    // Both discipline figures, and always both. A foul count that only appears
    // when somebody has fouled would make an empty column read as a clean
    // record on one screen and as a missing feature on the next.
    tagged.push({ value: t.fouls ?? 0, label: 'Fouls', tone: 'is-muted' });

    const cards = (t.yellowCards || 0) + (t.redCards || 0);
    tagged.push({ value: cards, label: 'Cards', tone: cards ? 'is-warn' : 'is-muted' });

    // The figure that survives uneven minutes, which is exactly the comparison
    // between a starter and a squad player. Below 45 minutes the denominator is
    // too small to divide into. Per full match as this site counts one — the
    // professional "per 90" would be a match these players never play.
    if (t.minutes >= 45) {
        tagged.push({
            value: ((t.goals + t.assists) / t.minutes * FULL_MATCH_MIN).toFixed(2),
            label: `G+A per ${FULL_MATCH_MIN} min`,
            tone: 'is-muted',
        });
    }

    // Their best afternoon — the thing a player actually opens their own page
    // to find, and the thing a coach reaches for first in a conversation.
    const best = played.reduce(
        (a, b) => (involvements(b) > involvements(a) ? b : a), played[0],
    );
    if (best && involvements(best) > 0) {
        tagged.push({
            value: involvements(best),
            label: `Best · ${best.opponentName || 'opponent'}`,
            tone: 'is-good',
        });
    }

    // Unknown is not short. A match with no clock cannot count as a full one,
    // and must not count against them either — it is simply not in the
    // denominator, which is why the card says nothing when there are none.
    const full = played.filter(
        (r) => knownMinutes(r) && (r.minutesPlayed || 0) >= FULL_MATCH_MIN,
    ).length;
    if (full) tagged.push({ value: full, label: 'Full matches', tone: 'is-muted' });

    const groups = [{
        id: 'tagged',
        title: 'The season',
        note: 'Tagged during the match.',
        rows: tagged,
    }];

    if (!t.cvMatches) return groups;

    const over = `Estimated from ${count(t.cvMatches, 'filmed match', 'filmed matches')}`;
    const seen = (value, label) => ({
        value, label, tone: 'is-muted', confidence: 'medium',
    });

    const ball = [seen(t.cvTouches ?? 0, 'Touches')];
    if (t.cvPassesAttempted) {
        ball.push(
            seen(`${Math.round((t.cvPassesCompleted / t.cvPassesAttempted) * 100)}%`,
                'Pass accuracy'),
            seen(t.cvPassesCompleted, 'Passes completed'),
        );
    }
    if (t.cvCarries) ball.push(seen(t.cvCarries, 'Carries'));
    if (t.cvShots) ball.push(seen(t.cvShots, 'Shots'));

    const running = [];
    if (t.cvDistanceM) {
        running.push(seen((t.cvDistanceM / 1000).toFixed(1), 'km covered'));
    }
    if (t.cvTopSpeedKmh) {
        running.push(seen(t.cvTopSpeedKmh.toFixed(1), 'Top speed km/h'));
    }
    if (t.cvSprintCount) running.push(seen(t.cvSprintCount, 'Sprints'));
    if (t.cvAccelerations) running.push(seen(t.cvAccelerations, 'Bursts'));
    if (t.cvTopAcceleration) {
        running.push(seen(t.cvTopAcceleration.toFixed(1), 'Hardest burst m/s²'));
    }

    const defending = [seen(t.cvTackles ?? 0, 'Tackles won')];
    if (t.cvInterceptions) defending.push(seen(t.cvInterceptions, 'Interceptions'));
    if (t.cvRecoveries) defending.push(seen(t.cvRecoveries, 'Recoveries'));

    groups.push(
        {
            id: 'ball',
            title: 'On the ball',
            note: `${over}. Passing is what the pipeline sees most of, because a `
                + 'pass is two touches with a ball between them.',
            rows: ball,
        },
        {
            id: 'running',
            title: 'Running',
            note: `${over}, over the minutes the tracker actually held on to ${them}.`,
            rows: running,
        },
        { id: 'defending', title: 'Defending', note: `${over}.`, rows: defending },
    );

    return groups.filter((group) => group.rows.length);
}

// --------------------------------------------------- the season, at a glance
//
// Two things a squad page can say from documents it has already loaded, and
// said neither of until now: how the last few games went, and what is still
// waiting to be done.
//
// Both are arithmetic over the match list and the roster, so both live here
// rather than in coach.js, and both are covered in tests/season.test.js. The
// second one especially: "what is outstanding" is a claim that a coach will act
// on, and a job that appears when it should not — or worse, does not appear
// when it should — is the kind of thing a browser check would never catch.

/** A finalized match, read as a result. Null when it was never played. */
function resultOf(match) {
    if (!match?.finalized) return null;
    const us = num(match.scoreUs) ?? 0;
    const them = num(match.scoreThem) ?? 0;
    if (us > them) return 'W';
    if (us < them) return 'L';
    return 'D';
}

/**
 * The last few results, oldest first — the way a form guide is always read.
 *
 * Only finalized matches. A fixture that has not been played is not a blank in
 * the run, it is simply not in it: five results with a gap in the middle would
 * say a match was played and produced nothing.
 *
 * The list arrives newest first (that is the order the matches tab wants), so
 * this takes from the front and reverses.
 */
export function formGuide(matches, limit = 5) {
    const played = (matches || []).filter((m) => m?.finalized);
    return played
        .slice(0, Math.max(0, limit))
        .map((match) => ({
            id: match.id ?? null,
            opponentName: match.opponentName || 'opponent',
            date: match.date || null,
            result: resultOf(match),
            scoreUs: num(match.scoreUs) ?? 0,
            scoreThem: num(match.scoreThem) ?? 0,
        }))
        .reverse();
}

/**
 * Days from one YYYY-MM-DD to another, or null if either is not one.
 *
 * Parsed as UTC noon rather than midnight local. Both ends of this comparison
 * are calendar dates with no time on them, and midnight is exactly where a
 * timezone offset flips one of them to the day before — which would put "today"
 * a day out for every coach west of Greenwich, which is all of them.
 */
export function daysBetween(from, to) {
    const parse = (value) => {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ''));
        if (!match) return null;
        return Date.UTC(+match[1], +match[2] - 1, +match[3], 12);
    };
    const a = parse(from);
    const b = parse(to);
    if (a == null || b == null) return null;
    return Math.round((b - a) / 86400000);
}

/**
 * The next fixture that has not been played, or null.
 *
 * "Next" means today or later. A match whose date has passed and which nobody
 * tagged is not the next fixture — it is a job, and `seasonJobs` above already
 * says so. Showing it here under "next up" would put a game that has already
 * been played at the top of the page every week until somebody dealt with it.
 *
 * Dateless fixtures are skipped rather than sorted to one end. A match with no
 * date cannot be shown as "in three days", and a card that says "next up" over
 * a blank is worse than no card.
 */
export function nextFixture(matches, today) {
    if (!today) return null;

    let best = null;
    let bestAway = null;
    for (const match of matches || []) {
        if (!match || match.finalized) continue;
        const away = daysBetween(today, match.date);
        if (away == null || away < 0) continue;
        if (bestAway == null || away < bestAway) {
            best = match;
            bestAway = away;
        }
    }

    if (!best) return null;
    return { match: best, daysAway: bestAway };
}

/** "today" / "tomorrow" / "in 4 days", from a day count. */
export function whenLabel(daysAway) {
    if (daysAway == null) return null;
    if (daysAway <= 0) return 'today';
    if (daysAway === 1) return 'tomorrow';
    if (daysAway < 7) return `in ${daysAway} days`;
    if (daysAway < 14) return 'next week';
    return `in ${Math.round(daysAway / 7)} weeks`;
}

/**
 * What is still waiting on the coach, most pressing first.
 *
 * Every one of these is a job with an obvious next action, which is the test
 * for whether it belongs here. "3 matches have no footage" is not a job — most
 * matches never will have — and a panel that lists the shape of the world
 * rather than the work teaches people to stop reading it.
 *
 * `today` is passed in rather than read from the clock, so a test can say what
 * day it is and so a fixture cannot go stale.
 */
export function seasonJobs({ matches = [], players = [], today = null } = {}) {
    const squad = players.filter((p) => p?.active !== false);
    const jobs = [];

    // Played and not published. The most pressing by a distance: the match is
    // over, the numbers exist, and every player is waiting on one press.
    const unpublished = matches.filter(
        (m) => !m?.finalized && m?.status === 'full_time',
    );
    if (unpublished.length) {
        jobs.push({
            id: 'publish',
            count: unpublished.length,
            title: `${count(unpublished.length, 'match', 'matches')} to publish`,
            note: 'Played and tagged. Nobody can see their report until you publish.',
            tab: 'matches',
        });
    }

    // A fixture whose date has passed and which nobody ever started tagging.
    // Only in the past: a match created for Saturday is not outstanding work on
    // Thursday, and listing it as such would make this panel noise every week.
    if (today) {
        const missed = matches.filter(
            (m) => !m?.finalized
                && (!m?.status || m.status === 'scheduled')
                && m?.date && m.date < today,
        );
        if (missed.length) {
            jobs.push({
                id: 'untagged',
                count: missed.length,
                title: `${count(missed.length, 'match', 'matches')} nobody tagged`,
                note: 'The date has passed and the tagging tool was never opened '
                    + 'for it. Minutes cannot be recovered later.',
                tab: 'matches',
            });
        }
    }

    // Players who have never signed in. They have a report and no way to read
    // it, which is the whole point of the portal.
    const unclaimed = squad.filter((p) => !p?.linkedUid);
    if (unclaimed.length) {
        jobs.push({
            id: 'invite',
            count: unclaimed.length,
            title: `${count(unclaimed.length, 'player')} not signed in`,
            note: 'They cannot see their own numbers until they accept an invite.',
            tab: 'roster',
        });
    }

    // A position is not an input to any figure — it is a heading over one — so
    // this is the least pressing of the four and sits last.
    const unplaced = squad.filter((p) => !positionOf(p?.position));
    if (unplaced.length) {
        jobs.push({
            id: 'position',
            count: unplaced.length,
            title: `${count(unplaced.length, 'player')} with no position`,
            note: 'The squad list groups by line, and these sit under '
                + '“No position set”.',
            tab: 'roster',
        });
    }

    return jobs;
}

/**
 * A player's own moments across the season: every goal they scored or set up
 * and every card they were shown, newest match first and in match order
 * within it.
 *
 * Read off the timelines already in their reports, which `playerTimeline`
 * built for them alone, so this can show nothing the match pages could not.
 * Each moment carries its report so the page can open that match. Reports
 * published before timelines existed simply contribute nothing.
 */
export function seasonMoments(reports, { limit = 12 } = {}) {
    const moments = [];
    for (const report of reports || []) {
        const own = (report.timeline || [])
            .filter((e) => e.mine && (e.type === 'goal' || e.type === 'card'))
            .sort((a, b) => (a.clockS || 0) - (b.clockS || 0));
        for (const entry of own) {
            const kind = entry.type === 'card' ? 'card'
                : /^You assisted/.test(entry.label) ? 'assist' : 'goal';
            moments.push({
                kind,
                label: entry.label,
                minute: `${Math.floor((entry.clockS || 0) / 60) + 1}′`,
                report,
            });
        }
    }
    return moments.slice(0, limit);
}
