// The two sides compared, and the team and keeper stat rows.
//
// Part of `report.js`, which re-exports it.

import { num, share } from './report-format.js?v=148';
import { possessionIsInPlay, shapeConfidence, xgTrust } from './report-quality-notes.js?v=148';

// ------------------------------------------------- comparing the two sides
//
// The pipeline has always measured both teams — `report_export.build_report_json`
// runs `team_stats` over TEAM_A and TEAM_B and publishes each — and until now
// the coach's report read only its own side. Twenty-five figures about the
// opposition, computed, published, and rendered nowhere. A possession of 58%
// says very little on its own and everything next to their 42%.
//
// Drawing the pair is where it gets easy to lie, because a bar is read before
// either number beside it. Three different claims live in the same list:
//
//   SHARE — a share of a continuous whole. Possession of time, and little else.
//           Their figure is the rest of ours, the boundary between them is the
//           entire story, and there is no count behind it to be short of.
//
//   COUNT — a share of some number of discrete events. Shots, tackles, corners.
//           Also a split — twelve shots to four really is three quarters of the
//           shots — but with a catch a share cannot express on its own: three
//           shots to one is also three quarters, and it is four shots in a
//           whole match. So a count carries whether its lead is bigger than
//           chance, and a bar that is not says so instead of looking decisive.
//
//   RATE  — each figure is a percentage of its own denominator. Pass accuracy
//           of 84% against 71% is not a 54/46 split of anything; the two do not
//           add up and must not be drawn as though they did. Each runs 0-100 on
//           its own scale.
//
//   LEVEL — a magnitude with no denominator at all. PPDA, and every shape
//           figure in metres. "38 metres wide" is not a share of anything and
//           neither side owns part of the other's, so the two are simply drawn
//           against whichever was larger. Splitting these is the worst of the
//           four mistakes available: a PPDA of 6.9 against 14.8 would read as
//           having had 32% of the pressing, which is not a quantity.
//
// `tally` in ui.js drew every row as a share, which is how the half-time page
// has been showing 84% against 71% as a near dead heat.

/** A share of a continuous whole — their figure is the rest of ours. */
export const SHARE = 'share';
/** A share of some number of discrete events, which may be too few to mean it. */
export const COUNT = 'count';
/** Each figure is a percentage of its own denominator, on a 0-100 scale. */
export const RATE = 'rate';
/** A magnitude with no denominator — metres, or passes per defensive action. */
const LEVEL = 'level';

/**
 * How much of the track each side's bar fills, and whether to believe it.
 *
 * Returns `{ us, them, mode, tentative }`, or null when there is nothing honest
 * to draw — a pair with a side missing is not a pair, and filling the other
 * half with zero would report a nil return the pipeline never saw.
 *
 * `mode` is what the renderer needs: `'split'` (the two fill the track between
 * them, boundary moving) or `'opposed'` (each grows from a fixed centre against
 * its own full). Shares and counts are both splits; only their honesty differs.
 */
export function comparePair(us, them, kind = COUNT) {
    const a = num(us);
    const b = num(them);
    if (a == null || b == null) return null;

    if (kind === RATE) {
        // Percentages of their own denominators, so full is 100. Both arrive in
        // the unit the row prints — 84, not 0.84 — so the bar and the number
        // beside it cannot come apart.
        return {
            us: clampPct(a), them: clampPct(b), mode: 'opposed', tentative: false,
        };
    }

    if (kind === LEVEL) {
        // No denominator, so full is whichever side was larger and the other is
        // drawn against it. Negative levels are not a thing any of these
        // measure, and a magnitude of nothing on both sides is nothing to draw.
        const scale = Math.max(Math.abs(a), Math.abs(b));
        if (!(scale > 0)) return null;
        return {
            us: clampPct((Math.abs(a) / scale) * 100),
            them: clampPct((Math.abs(b) / scale) * 100),
            mode: 'opposed',
            tentative: false,
        };
    }

    const total = a + b;
    // Two figures that are supposed to be a whole and sum to nothing are not a
    // whole. Half and half would draw a dead heat nobody measured.
    if (!(total > 0)) return null;

    return {
        us: (a / total) * 100,
        them: (b / total) * 100,
        mode: 'split',
        tentative: kind === COUNT && insideNoise(a, b),
    };
}

/**
 * Is this lead smaller than the one chance alone would hand out?
 *
 * Each event is a coin toss between the two sides under a null of no
 * difference, so the count on one side has standard deviation sqrt(n)/2 and the
 * gap between them has sqrt(n). Two of those is the usual bar, which lands at
 * `|a - b| < 2*sqrt(n)`:
 *
 *     3 shots to 1     gap 2   against 4.0   — inside the noise
 *     12 to 4          gap 8   against 8.0   — just outside it
 *     30 to 10         gap 20  against 12.6  — outside it
 *
 * The same reasoning `xgCalibration` uses on marked shots and `pressingRead`
 * uses on scored blocks, and for the same reason: a coach shown 75% of the
 * shots will read it as dominance whether it was four shots or forty.
 */
/**
 * Which side, if either, has earned being called the better one.
 *
 * Lives here rather than in the renderer because it is a judgement and not a
 * layout: it decides whether a coach sees a green number, which is the closest
 * thing these pages have to an opinion. The pure test suites can only load
 * modules that import no DOM, and a rule about when to state a verdict is
 * exactly the kind that should not go untested for want of a DOM.
 *
 * Returns 'ours-good', 'ours-bad' or null, matching the row classes.
 *
 * Two ways to earn nothing:
 *
 * - **the lead is inside the noise.** Handled upstream by `comparePair`, which
 *   is where the count that decides it lives.
 * - **the two print the same thing.** 10 possessions in 88 and 8 in 71 are
 *   different numbers and both print "11%". Colouring one of them puts a
 *   verdict beside its own disproof: the reader is looking at the evidence and
 *   it says the same thing twice.
 */
export function verdict(options = {}) {
    const { ours, theirs, usText, themText, better, tentative = false } = options;
    if (!better || tentative || ours == null || theirs == null) return null;

    const shown = (raw, formatted) => (formatted != null ? formatted : raw);
    if (shown(ours, usText) === shown(theirs, themText)) return null;
    if (ours === theirs) return null;

    const weLead = ours > theirs;
    return (better === 'high') === weLead ? 'ours-good' : 'ours-bad';
}

export function insideNoise(a, b) {
    const n = a + b;
    if (!(n > 0)) return true;
    return Math.abs(a - b) < 2 * Math.sqrt(n);
}

const clampPct = (value) => Math.max(0, Math.min(100, value));

/**
 * A tagged count, or an em dash when there is no log to have counted it.
 *
 * The scoreline is the loudest thing on both the match report and the half-time
 * page, and `aggregateMatch` initialises every count to zero — so a match nobody
 * ran the tablet for led with **0–0** in the largest type on the page. A match
 * that was filmed and not tagged is the ordinary case, not a corner one, and
 * nil-all is a result somebody could repeat out loud. Absent is not zero here
 * for the same reason it is not in `whistleFrom`, and louder.
 */
export const taggedCount = (count, log) => ((log || []).length ? (count ?? 0) : '—');

/**
 * The figures somebody tapped, as rows — for the match report and for the
 * half-time page, which used to build them separately and had drifted.
 *
 * The drift was not cosmetic. The coach's full post-match report carried nine
 * one-sided cards and the three-minute half-time read carried five two-sided
 * bars, so **the opponent's cards, the opponent's offsides and free kicks
 * appeared on the touchline page and were missing from the full report
 * altogether** — the fuller document said less than the triage one. Same shape
 * as `teamStatRows`, and for the same reason: one `pick` reads both sides, so
 * our column and theirs can never come off different fields.
 *
 * Throw-ins, goal kicks and out-of-bounds are tagged and deliberately left out
 * of both. They are the noise of a match rather than a read on it, and the
 * restart tags exist to tell the pipeline when the ball was dead.
 *
 * @param counts   `aggregateMatch(...).counts` — `{ us: {...}, them: {...} }`.
 * @param subs     Our substitutions. Never a pair: nobody tags the opposition's.
 * @param goals    Off at half-time, where the scoreline is already the biggest
 *                 thing on the page and that page's whole rule is not to report
 *                 what the coach watched happen.
 * @param dropEmpty Drop a row neither side registered. On at half-time, where
 *                 the reader is standing up; off in the report, where "we
 *                 conceded no corners" is a fact worth being able to look up.
 */
export function taggedTeamRows(counts, { subs = null, goals = true, dropEmpty = false } = {}) {
    const us = counts?.us || {};
    const them = counts?.them || {};

    const row = (label, pick, better) => {
        const usN = pick(us);
        const themN = pick(them);
        return {
            type: 'match', label, kind: COUNT, better,
            usN, themN, value: usN, themValue: themN,
        };
    };
    const of = (key) => (side) => side[key] ?? 0;

    const rows = [
        // Corners and free kicks are counted for whoever was awarded them;
        // fouls, cards and offside against whoever gave them away. The labels
        // have to carry that, because a pair of numbers cannot.
        ...(goals ? [row('Goals', of('goal'), 'high')] : []),
        row('Corners won', of('corner'), 'high'),
        row('Free kicks won', of('free_kick'), 'high'),
        row('Fouls committed', of('foul'), 'low'),
        row('Offside', of('offside'), 'low'),
        row('Cards', of('card'), 'low'),
    ].filter((r) => !dropEmpty || r.usN || r.themN);

    // Null, not zero, on their side: an untagged opposition substitution and no
    // opposition substitution are not the same thing, and this is the one row
    // here where the tablet was never asked the question.
    if (subs != null) {
        rows.push({
            type: 'match', label: 'Substitutions', kind: COUNT, better: null,
            usN: subs, themN: null, value: subs, themValue: null,
        });
    }
    return rows;
}

/**
 * The video-derived figures for the coach's own side, typed and labelled.
 *
 * `confidence` carries the marks the caller worked out from the quality block —
 * passed in rather than computed here because grading them lives in db.js,
 * which this module cannot import and stay testable.
 *
 * Reads `teams.team_a` only, which is always the coach's own side.
 */
export function teamStatRows(cv, confidence = {}) {
    const ours = cv?.teams?.team_a;
    if (!ours) return [];

    // The opposition's copy of the same measurements. Absent on a report
    // published before both sides were carried, and absent is not zero: every
    // row below degrades to a one-sided figure rather than drawing a bar that
    // says the other team did nothing.
    const theirs = cv?.teams?.team_b || null;

    const quality = cv.quality || {};
    const events = confidence.events || null;
    const possession = confidence.possession || null;
    const trust = xgTrust(cv.calibrationErrorM);

    /**
     * One comparable row.
     *
     * `pick` reads a figure off one side, so the same function reads both and
     * the two can never drift apart — the failure that matters here is not a
     * wrong number, it is our column and their column being taken from
     * different fields.
     */
    const row = (type, label, kind, pick, options = {}) => {
        const { format = (v) => v, better = null, confidence: mark = events,
                explained = false } = options;
        const usN = pick(ours);
        const themN = theirs ? pick(theirs) : null;
        return {
            type, label, kind, better, explained, confidence: mark,
            value: usN == null ? null : format(usN),
            themValue: themN == null ? null : format(themN),
            usN, themN,
        };
    };

    // Every figure a row carries is in the unit the row prints — 84 for a
    // percentage, not 0.84 — so a bar drawn from it can never disagree with the
    // number written beside it. The pipeline speaks in fractions; this is where
    // that stops.
    const asPct = (fraction) => (fraction == null ? null : fraction * 100);
    const pctText = (value) => `${Math.round(value)}%`;

    // A breakdown is only worth a percentage if it is a share of something this
    // run actually counted, and it has to be each side's own total — dividing
    // their forward passes by our attempts would be a number about nobody.
    const ofAttempted = (side, count) => {
        const total = side.passes_attempted || 0;
        return total && count != null ? asPct(count / total) : null;
    };

    // The funnel, read off the counts the pipeline publishes rather than off
    // percentages it has already worked out. Counts are what survive being
    // combined and what let a browser say a figure rests on too few
    // possessions to mean anything.
    const reached = (side, third) => {
        const phase = side.phase_of_play;
        const total = phase?.total || 0;
        const n = phase?.reached?.[third];
        return total && n != null ? asPct(n / total) : null;
    };
    const endedIn = (side, how) => {
        const phase = side.phase_of_play;
        const total = phase?.total || 0;
        const n = phase?.ended?.[how];
        return total && n != null ? asPct(n / total) : null;
    };
    // Out of the possessions that *started* at the back — not out of all of
    // them. A side that never won the ball in its own third did not fail to
    // play out from it, so this is null there rather than zero.
    const outFromTheBack = (side) => {
        const phase = side.phase_of_play;
        const started = phase?.started?.defensive || 0;
        const escaped = phase?.escaped_defence;
        return started && escaped != null ? asPct(escaped / started) : null;
    };
    const accuracyIn = (side, third) => {
        const phase = side.phase_of_play;
        const attempted = phase?.passes?.[third] || 0;
        const completed = phase?.passes_completed?.[third];
        return attempted && completed != null
            ? asPct(completed / attempted) : null;
    };

    return [
        {
            // The label carries the denominator, because the denominator
            // changed. With a tagged log the dead time is out of it and this is
            // possession of a ball that was in play; without one it is the
            // older, weaker figure and must not claim otherwise.
            ...row('possession',
                possessionIsInPlay(quality) ? 'Possession, ball in play' : 'Possession',
                SHARE, (t) => t.possession_pct,
                { format: share, better: 'high', confidence: possession }),
        },
        row('possession', 'Touches', COUNT, (t) => t.touches, { better: 'high' }),
        row('possession', 'Carries', COUNT, (t) => t.carries, { better: 'high' }),

        // Relabelled from "in your own third" now that the row has two owners:
        // each figure is that side's share of its own time on the ball, in its
        // own end of the pitch, so a possessive would be pointing at one of two
        // teams and the reader would have to guess which.
        row('possession', 'Own third', RATE, (t) => asPct(t.territory?.defensive),
            { format: pctText, confidence: possession, explained: true }),
        row('possession', 'Middle third', RATE, (t) => asPct(t.territory?.middle),
            { format: pctText, confidence: possession, explained: true }),
        row('possession', 'Opposition third', RATE, (t) => asPct(t.territory?.attacking),
            { format: pctText, confidence: possession, explained: true }),

        // --- phase of play ---
        //
        // The funnel. `territory` above says where the ball was; these say what
        // the side was trying to do there and whether it came off, which is the
        // distinction that made phase-of-play worth building separately rather
        // than as another slice of the same time.
        //
        // Every share is out of that side's own possessions, never out of the
        // match: a team with the ball twice as often would otherwise look twice
        // as good at moving it.
        row('phases', 'Possessions', COUNT, (t) => t.phase_of_play?.total || null,
            { confidence: possession }),
        row('phases', 'Reached midfield', RATE, (t) => reached(t, 'middle'),
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        row('phases', 'Reached the final third', RATE, (t) => reached(t, 'attacking'),
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        row('phases', 'Ended in a shot', RATE, (t) => endedIn(t, 'shot'),
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        // Its own denominator, and the row that actually answers "can we play
        // out from the back". The whole-funnel share above is flattered by
        // every possession that began in midfield already.
        row('phases', 'Out from the back', RATE, outFromTheBack,
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        // The point of the whole feature. A side at 92% in its own third and
        // 54% in the opposition's is a normal, healthy team; one overall figure
        // of 72% hides both halves, and 60% at the back is a problem nobody
        // would otherwise see.
        row('phases', 'Passing at the back', RATE, (t) => accuracyIn(t, 'defensive'),
            { format: pctText, better: 'high', explained: true }),
        row('phases', 'Passing in midfield', RATE, (t) => accuracyIn(t, 'middle'),
            { format: pctText, better: 'high', explained: true }),
        row('phases', 'Passing up front', RATE, (t) => accuracyIn(t, 'attacking'),
            { format: pctText, better: 'high', explained: true }),

        row('passing', 'Passes attempted', COUNT, (t) => t.passes_attempted || null,
            { better: 'high' }),
        row('passing', 'Pass accuracy', RATE, (t) => asPct(t.pass_accuracy),
            { format: pctText, better: 'high' }),
        row('passing', 'Progressive passes', COUNT, (t) => t.progressive_passes,
            { better: 'high' }),
        // How direct a side was, which is the question the buckets exist to
        // answer and which the raw counts do not. Shares of each side's own
        // attempts, so a side that passed less does not look less direct.
        // Neither direction is good: a long ball is a route one team chose.
        row('passing', 'Played forward', RATE,
            (t) => ofAttempted(t, t.passes_by_direction?.forward), { format: pctText }),
        row('passing', 'Played long', RATE,
            (t) => ofAttempted(t, t.passes_by_length?.long), { format: pctText }),
        row('passing', 'Switches of play', COUNT, (t) => t.switches, { better: 'high' }),

        row('attacking', 'Final-third entries', COUNT, (t) => t.final_third_entries,
            { better: 'high' }),
        row('attacking', 'Entries into the box', COUNT, (t) => t.box_entries,
            { better: 'high' }),
        row('attacking', 'Crosses', COUNT, (t) => t.crosses, { better: 'high' }),
        row('attacking', 'Shots', COUNT, (t) => t.shots, { better: 'high' }),
        row('attacking', 'Shots on target', COUNT, (t) => t.shots_on_target,
            { better: 'high' }),
        // Withheld, not zeroed, when the calibration is too loose to support it.
        // A team total averages a lot of per-shot noise away, which is why it
        // survives a band that per-shot xG does not — but not every band.
        row('attacking', 'Expected goals', COUNT,
            (t) => (trust === 'none' ? null : t.xg),
            { format: (v) => v.toFixed(2), better: 'high' }),

        row('defending', 'Tackles', COUNT, (t) => t.tackles, { better: 'high' }),
        row('defending', 'Interceptions', COUNT, (t) => t.interceptions,
            { better: 'high' }),
        row('defending', 'Recoveries', COUNT, (t) => t.recoveries, { better: 'high' }),
        row('defending', 'Ground duels', COUNT, (t) => t.duels, { better: 'high' }),
        // Passes allowed per defensive action. Not a share of anything — the
        // two sides do not divide a fixed quantity of pressing between them —
        // so it is drawn against whichever was larger. Fewer means pressing
        // harder, so the low side is the good one and the bar has to be told.
        row('defending', 'Passes allowed per tackle (PPDA)', LEVEL, (t) => t.ppda,
            { format: (v) => v.toFixed(1), better: 'low' }),
        // The giveaways that turn straight into a chance against you. A single
        // turnover count cannot say this, which is why it is counted by third.
        row('defending', 'Lost in own third', COUNT,
            (t) => t.turnovers_by_third?.defensive, { better: 'low' }),

        ...shapeStatRows(ours.shape, theirs?.shape, cv.calibrationErrorM),

        ...keeperStatRows(cv.keepers, {
            events, shape: shapeConfidence(cv.calibrationErrorM),
        }),
    ];
}

/**
 * How spread out each side played, in metres.
 *
 * `report_json` used to publish one shape built from every track on the pitch,
 * both teams and the referee together, and label it Team A's. It is now built
 * per team, and both are read here — a side that was 8m narrower than the
 * opposition is a fact about the match, where "38m wide" on its own is a number
 * with nothing to lean on.
 *
 * Empty until a calibration exists, which is every run today — width in metres
 * is not something a pixel can answer. Their shape may be absent while ours is
 * not, and then the rows draw one-sided rather than against a zero.
 *
 * None of these is coloured good or bad. A compact side is well-drilled or it
 * is pinned in its own half, and no number here can tell the difference.
 */
export function shapeStatRows(shape, theirShape, calibrationErrorM) {
    if (!shape || shape.width_m == null) return [];
    const band = shapeConfidence(calibrationErrorM);
    const metres = (value) => `${Math.round(value)}m`;

    const row = (label, key) => {
        const usN = shape[key] ?? null;
        const themN = theirShape?.[key] ?? null;
        return {
            type: 'shape', label, kind: LEVEL, better: null, explained: true,
            confidence: band,
            value: usN == null ? null : metres(usN),
            themValue: themN == null ? null : metres(themN),
            usN, themN,
        };
    };

    return [
        row('Average width', 'width_m'),
        row('Average depth', 'depth_m'),
        // Mean distance from each player to their own team's centre.
        row('Compactness', 'compactness_m'),
        // Metres from the goal this side was defending to the mean of its
        // deepest few outfielders. The one row here that can be absent while
        // the others are present: it needs somebody to have said which goal
        // each colour defends, and it needs enough of the side tracked at once
        // for the deepest few to be a back line rather than whoever was on
        // screen. `groupStats` drops it when both sides are null, so an
        // uncalibrated or unassigned run shows three rows rather than a fourth
        // reading nothing.
        row('Defensive line', 'line_m'),
    ];
}

/**
 * The goalkeeper's half of the match, for both goalkeepers.
 *
 * `pitchiq/keeper.py` has computed this block since the pipeline's second month and
 * until today no page drew a single figure of it. It reached the browser the
 * whole time — `readCvStats` spreads the summary document, so it has been
 * sitting on `state.match.cv.keepers` unread.
 *
 * What is deliberately not here: shots faced and goals conceded. Both are the
 * opposition's shots and the opposition's goals, and both are already on the
 * screen in the attacking group with the columns the other way round. Printing
 * them again under a different name would look like a second measurement and
 * would be the same one.
 *
 * Almost nothing here is marked good or bad. A keeper with eight saves was
 * either excellent or abandoned by the ten in front of him, and a count cannot
 * tell those apart. Save percentage is the exception: it already has the
 * workload divided out of it.
 *
 * One keeper per team, both optional and either one able to be the missing
 * one. `identify_keepers` finds a keeper per team independently — a side whose
 * keeper wore a colour close enough to his outfielders can be the only side
 * without one — so the rows draw from whichever were found and leave the other
 * column empty rather than against a zero. `groupStats` keeps a row on one
 * side alone for exactly this reason.
 */
export function keeperStatRows(keepers, confidence = {}) {
    const forTeam = (team) => (keepers || []).find((k) => k && k.team === team) || null;
    const ours = forTeam('team_a');
    const theirs = forTeam('team_b');
    if (!ours && !theirs) return [];

    const events = confidence.events || null;
    // The metre figures rest on the homography, not on the event detector, so
    // they carry the calibration's band instead of the event band.
    const shape = confidence.shape || null;

    const row = (label, kind, pick, options = {}) => {
        const { format = (v) => v, better = null, confidence: mark = events,
                explained = false } = options;
        const usN = ours ? (pick(ours) ?? null) : null;
        const themN = theirs ? (pick(theirs) ?? null) : null;
        return {
            type: 'keeping', label, kind, better, explained, confidence: mark,
            value: usN == null ? null : format(usN),
            themValue: themN == null ? null : format(themN),
            usN, themN,
        };
    };

    const asPct = (fraction) => (fraction == null ? null : fraction * 100);
    const pctText = (value) => `${Math.round(value)}%`;
    const metres = (value) => `${Math.round(value)}m`;

    // A maximum over no sweeper actions is not zero metres. `to_json` sends
    // null for it now; reports published before it did send 0.0, and this keeps
    // those from drawing a keeper who never left his line as one who came out
    // and got exactly nowhere.
    const furthest = (k) => (k.sweeper_actions ? k.sweeper_max_distance_m : null);

    return [
        row('Saves', COUNT, (k) => k.saves),
        // Saves out of the shots that actually reached the line — saves plus
        // goals conceded, not shots faced. A keeper who was never tested has no
        // percentage at all, which `groupStats` turns into a dash.
        row('Save percentage', RATE, (k) => asPct(k.save_pct),
            { format: pctText, better: 'high', explained: true }),
        row('Claims', COUNT, (k) => k.claims),
        row('Sweeper actions', COUNT, (k) => k.sweeper_actions),
        row('Furthest from goal', LEVEL, furthest,
            { format: metres, confidence: shape }),
        row('Distributions', COUNT, (k) => k.distributions),
        // Each accuracy is out of that kind of restart alone, so a keeper who
        // never punted has no punt figure rather than a nought per cent. The
        // kinds come from hold time and distance, never from ball height, so a
        // ball the pipeline could not classify is in none of the three.
        row('Kick accuracy', RATE, (k) => asPct(k.kick_accuracy),
            { format: pctText, better: 'high', explained: true }),
        row('Punt accuracy', RATE, (k) => asPct(k.punt_accuracy),
            { format: pctText, better: 'high', explained: true }),
        row('Throw accuracy', RATE, (k) => asPct(k.throw_accuracy),
            { format: pctText, better: 'high', explained: true }),
        row('Average kick', LEVEL, (k) => k.mean_kick_distance_m,
            { format: metres, confidence: shape }),
        row('Average punt', LEVEL, (k) => k.mean_punt_distance_m,
            { format: metres, confidence: shape }),
    ];
}

/**
 * Which team’s goalkeeper a tracked figure was judged to be, or null.
 *
 * `identify_keepers` picks a track per team out of kit colour and time spent in
 * front of a goal, and every figure in the block above is measured about
 * whoever it picked. `track_ids` is how that judgement leaves Python, and until
 * now it was the one field of the keeper block no page read — so the premise
 * under eleven rows was the only thing on the match view a human could not
 * check. A wrong pick does not look wrong. It looks like a keeper who never
 * claimed a cross.
 *
 * Track ids arrive as numbers out of Python and as strings out of anything
 * that has been through a document key, so both sides are compared as numbers.
 */
export function keeperOfTrack(keepers, trackId) {
    const wanted = Number(trackId);
    if (trackId == null || !Number.isFinite(wanted)) return null;
    const found = (keepers || []).find((k) => k
        && (k.track_ids || []).some((id) => Number(id) === wanted));
    return found ? found.team : null;
}
