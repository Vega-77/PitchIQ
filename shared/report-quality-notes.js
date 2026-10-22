// What the figures rest on: the notes under the video figures.
//
// Part of `report.js`, which re-exports it.

import { count } from './report-format.js?v=150';

// ------------------------------------------------- what the figures rest on
//
// Both the coach's match view and the half-time view print a line under the
// video-derived numbers saying what limited them. They used to build that line
// separately from two of the available figures, which is how the two pages
// drifted apart and how four new quality fields ended up reaching neither.
// Built here instead, with no DOM, so the wording is one thing and can be
// tested against a quality block directly.

/**
 * Whether the pipeline was told when the ball was out of play.
 *
 * `live_share` is null — not zero — when no tagged log reached the run, and
 * that difference changes what the possession figure beside it *means*. With a
 * log, dead time is out of the denominator and the share is a share of
 * football. Without one, a player standing over the ball waiting to take a
 * throw-in is a very clear holder, and every second of that counts as
 * possession. Same arithmetic, two different claims, so the label has to know
 * which one it is making.
 */
export function possessionIsInPlay(quality) {
    return (quality?.live_share ?? quality?.liveShare) != null;
}

/**
 * What a printed sheet says about itself, along its top edge.
 *
 * A page that has left the app has left everything that made it readable: the
 * navigation saying whose account it came from, the tab title saying which
 * match, and the reader's own memory of having clicked through to it. Three
 * months later it is a piece of paper with a teenager's name and some numbers
 * on it, and the two questions somebody will ask of it — *whose is this, and
 * how old is it* — are exactly the two the screen never had to answer.
 *
 * The date is when the sheet was printed, not when the match was played. Both
 * matter and they are not the same fact: a report re-printed after a coach
 * corrected the review says something different from the one printed the night
 * of the game, and only the printing date distinguishes them.
 *
 * `subject` is the person or team the page is about, `matchLine` the fixture.
 * Either may be missing — a page is still worth stamping with the half of the
 * answer it has.
 */
export function printStamp(options = {}) {
    const { subject, matchLine, printedAt = new Date(), estimated = false } = options;

    const parts = [];
    if (subject) parts.push(subject);
    if (matchLine) parts.push(matchLine);

    const day = printedAt instanceof Date ? printedAt : new Date(printedAt);
    const printed = Number.isNaN(day.getTime())
        ? null
        : day.toLocaleDateString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric',
        });
    if (printed) parts.push(`printed ${printed}`);

    // Not a footnote. Whatever caveat block travelled onto the page can be a
    // sheet away by the time a number is read out loud, and this line is on
    // every one of them.
    //
    // It also has to say what the little marks are. On screen `cvMark` carries
    // its explanation in a title attribute; on paper it is three dots beside a
    // number, which is worse than no mark at all — it looks like a footnote
    // reference to a footnote that is not there.
    if (estimated) {
        parts.push('figures marked ··· were estimated from video, not tapped');
    }

    return parts.join(' · ');
}

/** Seconds as "4m 20s", or "20s" under a minute. For prose, not for a table. */
export function roughDuration(seconds) {
    const total = Math.round(seconds || 0);
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    const rest = total % 60;
    return rest ? `${mins}m ${rest}s` : `${mins}m`;
}

/**
 * How much to trust a figure measured in metres.
 *
 * Everything else on a match page is graded by `cvConfidence` on ball coverage
 * and touch confidence, because everything else is built from finding the ball.
 * Team shape is not: it is the players' own positions run through the
 * homography, so it stands or falls on the calibration and grading it on ball
 * coverage would answer a question nobody asked about it.
 *
 * The bands are the old calibrate page's own, kept when it left the site on
 * 2026-10-07 — its `renderQuality` called a fit good at 0.5m mean error, and
 * a second standard for the same number would mean a coach could be told the
 * fit is good in one place and doubted in another. The calibrate page that
 * came back (`calibrate/calibrate.js`, GOOD_M and POOR_M) keeps the same two.
 */
export function shapeConfidence(calibrationErrorM) {
    if (calibrationErrorM == null) return 'low';
    if (calibrationErrorM <= 0.5) return 'high';
    if (calibrationErrorM <= 1.5) return 'medium';
    return 'low';
}

// Metres of mean calibration error at which per-shot xG, and then xG at all,
// stop being worth printing. Both come off the measured noise table in
// tests/test_xg_noise.py rather than from taste.
//
// The per-shot limit tightened from 1.0m to 0.5m on 2026-08-06, when the model
// was retrained without `shot_height`. Dropping a feature made the rest carry
// more of the answer, so position error moves the result further: a single
// shot's 95th-percentile shift is half the quantity at 0.5m and 89% of it at
// 1.0m. A number whose error bar is nearly as wide as itself is not a number.
const XG_PER_SHOT_LIMIT_M = 0.5;
const XG_TOTAL_LIMIT_M = 4.0;

/**
 * How much of the xG on a run is worth showing: `'shot'`, `'total'` or `'none'`.
 *
 * The model was measured against deliberately noisy positions, and the answer
 * was not flattering. On a 0.188 baseline, half a metre of position error moves
 * a single shot by 0.030 on average and 0.095 at the 95th percentile — half the
 * quantity; at one metre the p95 shift is 89% of it and at two metres it is
 * **larger than the quantity itself**.
 *
 * Both bands are measured rather than chosen, and they are measured on
 * different things, because they are claims about different numbers:
 *
 *   - `'shot'` — up to **0.5m**, which is also the fit `shapeConfidence` calls good.
 *     Good enough for "that was a decent chance". Not good enough to rank two
 *     shots 0.1 apart, which is why the caveat stays.
 *   - `'total'` — up to **4m**. Per-shot errors are independent and mostly
 *     cancel: simulated over a half's six shots, the *total* lands within 8% of
 *     the truth at 0.5m, 12% at 1m, 18% at 2m and 26% at 4m, while a single
 *     shot at 1m is already carrying an error bar nearly as wide as itself.
 *   - `'none'` — beyond four metres, when even the total is not worth printing.
 *     Printing it with a warning attached would still leave a specific-looking
 *     figure on screen, which is what people remember.
 *
 * A null error is not a good error and is not treated as one — but it is also
 * not evidence of a bad fit, so it lands on `'total'`: the reading that stays
 * true across the whole band it might be in.
 */
export function xgTrust(calibrationErrorM) {
    if (calibrationErrorM == null) return 'total';
    if (calibrationErrorM <= XG_PER_SHOT_LIMIT_M) return 'shot';
    if (calibrationErrorM <= XG_TOTAL_LIMIT_M) return 'total';
    return 'none';
}

/**
 * What limited a video-derived run, worst news first, as plain sentences.
 *
 * Caveats and denominators only. Nothing here congratulates the run on what
 * went well — a coach reading this line is being told what not to trust, and
 * padding it with good news is how the bad news stops being read. The one line
 * that is not a complaint is the live share, and that is there because it says
 * what the possession figure above was divided by.
 */
/** How a half reads in a sentence. */
const PERIOD_WORDS = {
    first_half: 'the first half',
    second_half: 'the second half',
};

/**
 * The caveat about which half this is — an array, usually empty.
 *
 * Empty when the tagged log settled it, because a correct answer arrived at
 * correctly is not a caveat and a note that fires on every run stops being
 * read. `'flag'` is quiet too: somebody typed it, so somebody knows.
 *
 * `'default'` is the one that speaks. It means nothing said which half this
 * was, so the pipeline assumed the first — and on second-half footage that
 * assumption mirrors every pitch drawing in the report without changing how
 * any of them look.
 */
export function periodNote(period, source) {
    if (source !== 'default') return [];
    const half = PERIOD_WORDS[period] || 'the first half';
    return [`nothing said which half this was, so it is drawn as ${half}`
        + ' — if it was the other one, every pitch picture here is mirrored'];
}

/**
 * Where `pitchiq/metrics.py` stops reporting bursts. Mirrored here only to word the
 * caveat; the pipeline is what actually withholds them, and a browser that
 * disagreed with it would caption a figure that is not on the page.
 */
export const ACCEL_NOISE_CEILING_M = 0.3;

/**
 * Phantom metres a minute, per metre of positional wobble.
 *
 * **Only for reports written before schema 7.** It was a single number because
 * the pipeline smoothed every track over a fixed nine frames; the window is now
 * fitted to each track's own measured wobble, so the phantom rate stopped being
 * proportional to the wobble and stopped being derivable from it. A version 7
 * report publishes `phantom_m_per_minute` and this is not consulted.
 *
 * Kept rather than deleted because reports already in Firestore do not have the
 * new field, and a coach opening last month's match should get the caveat that
 * was true of it — 353σ was measured against the smoothing that produced those
 * numbers, and it still describes them.
 */
const PHANTOM_M_PER_MINUTE = 353;

/**
 * What the wobble on one player's own track cost them, in a sentence.
 *
 * `cvQualityNotes` says a version of this to a coach, off the run's team-wide
 * noise figure. That is the wrong number for a player's own page twice over.
 * A team average is not this player's track — the tracker holds a centre-back
 * standing in space far better than a winger crossing in front of the crowd —
 * and, more importantly, the decision it explains is made per track:
 * `pitchiq/metrics.py` withholds the burst count when *that track's* noise passes
 * MAX_ACCEL_NOISE_M, so a player whose Bursts card is missing while the team
 * average sat under the ceiling would read the coach's note and conclude the
 * card was missing for some other reason.
 *
 * Second person throughout, because the player page is its only caller and a
 * third-person variant nobody renders is precisely the kind of unread figure
 * this note was written to explain.
 *
 * Deliberately quotes no phantom-metres rate. The pipeline fits each track's
 * smoothing window to its own wobble and publishes the resulting rate for the
 * run as a whole, not per player, so a rate worked out here would be quoting a
 * window this file has no way to know about — the same trap `cvQualityNotes`
 * documents at its own noise note.
 *
 * Returns null when there is no measured wobble, which is not the same as a
 * track that held perfectly still: a fragment too short to measure one is a
 * question nobody answered, and answering it here with "0.00m" would claim a
 * precision that was never established.
 */
export function playerWobbleNote(noiseM) {
    if (noiseM == null || !(noiseM > 0)) return null;

    const wobble = `Where the video put you wobbled about ${noiseM.toFixed(2)}m `
        + 'from frame to frame — the tracker being unsure where you were, not '
        + 'you moving';
    if (noiseM <= ACCEL_NOISE_CEILING_M) {
        return `${wobble}, so the metres here carry a little of it as well as `
            + 'your running.';
    }
    return `${wobble}. Past ${ACCEL_NOISE_CEILING_M}m a count of bursts is a `
        + 'count of that wobble, so there is none for you in this match, and '
        + 'the metres here are the roughest figure on the page.';
}

/**
 * What the stretches with no ball in them were, as a bar's worth of segments.
 *
 * `no_ball_s` has always been one figure covering two things a coach would
 * never put in the same sentence: the ball spending eleven seconds in a
 * teenager's hands behind the touchline, and twenty seconds of live football
 * nobody could see. Only the second is a hole in the report — nothing in it
 * reached possession, territory or the event list — and added together the
 * first hides it. Worse, it hides it *more* the better the tagging was.
 *
 * Returns null when there was nothing unseen, or when no tagged log reached the
 * run so nothing sorted it. The second case is deliberately not drawn as one
 * undifferentiated bar: a bar whose whole length is "unknown" claims a split
 * was measured and came out that way. The quality note says so in words
 * instead, which is what it is for.
 */
export function blindSplit(quality) {
    const q = quality || {};
    const blind = q.blind || null;
    if (!blind) return null;

    const totalS = blind.total_s ?? blind.totalS ?? 0;
    if (!(totalS > 0)) return null;

    // The longest single stretch nothing accounted for, and where in the
    // footage it starts. The same total lost as one blackout or as a hundred
    // flickers are different failures, and only the first takes a passage of
    // football with it — so the worst one travels beside the split rather than
    // being averaged into it. Published longest-first by pitchiq/ball_visibility.py.
    const first = (blind.worst || [])[0] || null;
    const worst = first ? {
        startS: first.start_s ?? first.startS,
        durationS: first.duration_s ?? first.durationS,
    } : null;

    const checked = Boolean(blind.checked);
    if (!checked) return { totalS, checked, worst, unexplainedS: null, segments: [] };

    const pick = (a, b) => blind[a] ?? blind[b] ?? 0;
    const parts = [
        { key: 'dead', label: 'Ball was out of play', seconds: pick('dead_s', 'deadS') },
        {
            key: 'accounted',
            label: 'Tagged something nearby',
            seconds: pick('accounted_s', 'accountedS'),
        },
        {
            key: 'unexplained',
            label: 'Live play, unaccounted for',
            seconds: pick('unexplained_s', 'unexplainedS'),
        },
    ];

    // Shares of the blind time, not of the match. Taken over the parts rather
    // than over `total_s` so the bar always fills its track: the three come
    // from the same partition and should sum to the total, and a rounding
    // difference must not leave a sliver of bare track that reads as a fourth
    // unnamed category.
    const sum = parts.reduce((acc, part) => acc + part.seconds, 0);
    return {
        totalS,
        checked,
        worst,
        unexplainedS: pick('unexplained_s', 'unexplainedS'),
        segments: parts
            .filter((part) => part.seconds > 0)
            .map((part) => ({ ...part, share: sum > 0 ? part.seconds / sum : 0 })),
    };
}

export function cvQualityNotes(quality, options = {}) {
    const q = quality || {};
    const { calibrated = false } = options;
    const pct = (value) => `${Math.round(value * 100)}%`;
    // 29.97 is a real frame rate and "29.97 of 59.94" is not a sentence.
    const round1 = (value) => String(Math.round(value * 10) / 10);
    const notes = [];

    // Seen, not "has a position for" — the rest were drawn in between
    // sightings, and calling a straight line "visible" would overstate what the
    // video actually showed. The seconds say the same thing as the percentage,
    // in the unit a coach can picture, so they travel together rather than as
    // two sentences that would read as two separate problems.
    // The unseen total is worth much less than the part of it that was
    // football. A well-tagged half is full of throw-ins nobody could have
    // filmed the ball through, and quoting the raw figure makes that half look
    // worse than one where the tagger never turned up. So when the run was
    // checked, the sentence ends on the part that is actually a hole.
    const seen = q.ball_seen_share ?? q.ballSeenShare;
    const noBall = q.no_ball_s ?? q.noBallS;
    const blind = blindSplit(q);
    let lost = '';
    if (blind && blind.checked) {
        lost = `of the ${roughDuration(blind.totalS)} with no ball in sight, `
            + `${roughDuration(blind.unexplainedS)} was live play nothing accounts for`;
    } else if (noBall) {
        lost = `${roughDuration(noBall)} of the clip with no ball in sight`;
    }
    if (seen != null) {
        notes.push(`the ball was visible in ${pct(seen)} of frames`
            + (lost ? ` — ${lost}` : ''));
    } else if (lost) {
        notes.push(lost);
    }

    // Only when the run skipped frames. Saying "30 of 30" is noise; saying
    // "15 of 60" is the single most important fact about how everything below
    // was produced, and a report that skipped frames used to look identical to
    // one that did not.
    //
    // Stated flatly rather than as a warning, because it was measured and it
    // costs under a percent of any distance down to six a second — see
    // tests/test_sampling.py. The pipeline raises a real warning below that
    // floor, and this note is not the place to repeat it.
    const sourceFps = q.source_fps ?? q.sourceFps;
    const sampleFps = q.sample_fps ?? q.sampleFps;
    if (sourceFps && sampleFps && sourceFps > sampleFps + 0.01) {
        notes.push(`read at ${round1(sampleFps)} of the footage's `
            + `${round1(sourceFps)} frames a second`);
    }

    // Only when the run was slower than the football, and phrased as the thing
    // a coach actually experienced rather than as a ratio. The half-time
    // whistle is the one deadline in this project; a factor of 1.4 means the
    // report a coach was waiting for at the break arrived eighteen minutes into
    // the second half, and "1.4x real time" does not say that to anyone.
    //
    // Silent when it keeps up. A batch report produced the following morning is
    // not improved by being told it could have been live, and the run that
    // matters here is the one somebody was standing about waiting for.
    const factor = q.realtime_factor ?? q.realtimeFactor;
    if (factor != null && factor >= 1) {
        notes.push('this took longer to work out than the football it watched — '
            + `about ${roughDuration((factor - 1) * 45 * 60)} behind a live half`);
    }

    if (!calibrated) notes.push('no pitch calibration, so nothing is in metres');

    // The camera moving is not one more caveat among the others — it is the
    // precondition for all of them. The homography is fitted from a single
    // frame, so once the camera has moved every distance, speed, shot position
    // and heatmap after that moment describes a pitch it is no longer pointed
    // at. Said with the minute, because "the camera moved" is a fact and "from
    // 34:12 onwards" is something a coach can act on: that is the half of the
    // match to disbelieve, and the tripod to check before the next one.
    const camera = q.camera;
    if (calibrated && camera?.moved && camera.first_s != null) {
        notes.push(`the camera moved ${roughDuration(camera.first_s)} into the footage`
            + ' — everything in metres after that is measured against the wrong pitch');
    }

    // How much of the pitch was ever in frame — the other question about the
    // same homography, and the one that changes how a *share* reads rather than
    // whether a distance is right.
    //
    // Worth saying because an unseen third does not look unseen. Territory
    // divides possession across the thirds, so a band the camera never held
    // contributes no seconds and comes out as a side that did not go there;
    // a heatmap draws it cold. Both are readings a coach would act on.
    //
    // Only when there is something to say: a camera that framed the pitch is
    // the expected case and a line confirming it would be noise in a list whose
    // whole job is caveats.
    const coverage = q.pitch_coverage;
    if (calibrated && coverage && !coverage.complete) {
        const blind = ['left', 'right'].filter(
            (end) => (coverage.goalmouths?.[end] ?? 1) < 0.6,
        );
        notes.push(blind.length
            // The louder failure, and it is not a matter of degree: shots at
            // that end were not undercounted, they were never seen, and the
            // shot map and every xG behind it are missing them entirely.
            ? `${count(blind.length, 'goalmouth')} never in shot — no shot at`
              + ` ${blind.length > 1 ? 'either' : 'that'} end was seen`
            : `the camera framed ${Math.round(coverage.visible_share * 100)}% of`
              + ' the pitch — shares of the pitch are shares of that part of it');
    }

    // Which half, and only when nothing but a default said so.
    //
    // The period decides which goal each side was attacking, and every pitch
    // picture on these pages is drawn from that: get it wrong and the shot
    // maps, the heatmaps, the pressing zone and the passing network are all
    // mirrored, and every one of them still looks right. When the tagged log
    // answered it there is nothing to say. When nothing did, the reader is
    // looking at an assumption and should be told which one.
    notes.push(...periodNote(options.period, options.periodSource));

    // Absent is not zero, again. No log means stoppages are still being counted
    // as football, and saying so is the difference between a coach reading the
    // possession split as a fact and reading it as an estimate with a known
    // bias in a known direction.
    const live = q.live_share ?? q.liveShare;
    const stoppages = q.stoppages ?? null;
    if (live == null) {
        notes.push('no tagged log reached this run, so stoppages still count as play');
    } else {
        notes.push(`the tagged log puts ${pct(live)} of it in play`
            + (stoppages ? ` across ${stoppages} stoppage${stoppages === 1 ? '' : 's'}` : ''));
    }

    // Two records of the same match, and how often they told the same story.
    // Not an accuracy: both can be wrong about the same moment in the same
    // direction, and this would call that agreement. What it is good for is the
    // trend — a run of matches where the two drift apart means something
    // changed, and it is worth knowing which before believing the rest.
    const rec = options.reconciliation;
    const goalRate = rec?.goal_agreement ?? rec?.goalAgreement;
    const goals = rec?.goals || {};
    const compared = (goals.agreed || 0) + (goals.cv_only || 0) + (goals.tag_only || 0);
    if (goalRate != null && compared) {
        notes.push(`the video and the tagged log agree on ${goals.agreed || 0} `
            + `of ${compared} goal${compared === 1 ? '' : 's'}`);
    }

    // The other half of the same comparison, and the half whose caveat has to
    // travel with it. A ball exit is found by projecting the tracked ball
    // through the calibration and watching it cross a touchline or a byline, so
    // this rate rests on the ball detection and not on the tagging — a run
    // that loses the ball for ten seconds at a time misses exits the tagger
    // caught, and every one of those arrives here looking like the tagger and
    // the video disagreed. Read it as a trend across matches. It is not a mark
    // out of ten for either record.
    //
    // Counted, not just rated: a hundred stoppages and six are different
    // sentences at the same percentage, and only one of them is worth acting
    // on.
    const exitRate = rec?.exit_agreement ?? rec?.exitAgreement;
    const exits = rec?.exits || {};
    const exitsSeen = (exits.agreed || 0)
        + (exits.cv_only || 0) + (exits.tag_only || 0);
    const exitsChecked = rec?.exits_checked ?? rec?.exitsChecked;
    if (exitRate != null && exitsSeen) {
        notes.push(`they agree on ${exits.agreed || 0} of ${exitsSeen} `
            + `ball${exitsSeen === 1 ? '' : 's'} going out of play, which rests`
            + ' on the ball detection rather than on the tagging');
    } else if (rec && exitsChecked === false) {
        // Absent is not zero here either, and these are two different absences.
        // Nothing crossed a line nobody could locate: with no calibration there
        // is no touchline in the frame to cross, so the tagged stoppages went
        // unchecked rather than unmatched. A reader told only that the rate is
        // missing would reasonably assume the check ran and found nothing.
        notes.push('nothing checked the tagged stoppages against the video —'
            + ' seeing a ball cross a line needs a calibration first');
    }

    // How far the tracked position wobbled, and what that costs.
    //
    // Every figure in metres on these pages rests on this, and until now
    // nothing measured it. It is stated as its consequence rather than as a
    // number alone, because 0.14m means nothing to a coach and "a player
    // standing still is credited with running" means a great deal.
    //
    // The rate is measured, not estimated, and it is now measured *there*: the
    // pipeline picks each track's smoothing window from its own wobble, so it
    // is the only place that knows both halves of the arithmetic. Reading the
    // published figure instead of re-deriving it is what stops this file
    // quoting a rate for a window it has no way to know about.
    //
    // A moving player is inflated far less than a still one, because a real
    // step dominates the wobble added to it — +0.5% on a jog at 0.20m against
    // 22m a minute from nothing. So it is quoted against standing still rather
    // than as a percentage of a distance total.
    const noise = q.position_noise_m ?? q.positionNoiseM;
    if (noise != null && noise > 0) {
        const published = q.phantom_m_per_minute ?? q.phantomMPerMinute;
        const phantomM = Math.round(published ?? noise * PHANTOM_M_PER_MINUTE);
        const smoothing = q.smoothing_s ?? q.smoothingS;
        // Named because it is the one figure on this page that describes a
        // decision rather than a measurement, and a coach comparing two matches
        // is entitled to know the second was smoothed harder than the first.
        const over = smoothing ? `, smoothed over ${smoothing.toFixed(1)}s` : '';
        notes.push(`the tracked position wobbles about ${noise.toFixed(2)}m frame `
            + `to frame${over}, which still credits a player standing still with `
            + `about ${phantomM}m a minute`);
        if (noise > ACCEL_NOISE_CEILING_M) {
            notes.push('too much wobble to count bursts — past about '
                + `${ACCEL_NOISE_CEILING_M}m the count is a count of the jitter`);
        }
    }

    // Carried in the counts above, not removed from them. Without a
    // calibration there is no goalmouth to measure anyone against, so a referee
    // and a goalkeeper are identical on every feature the classifier has, and
    // it keeps both rather than risk deleting a player. That is the right
    // trade, and it is still a caveat: somebody who is not playing may be
    // inside these numbers.
    const officials = q.flagged_officials ?? q.flaggedOfficials;
    if (officials) {
        notes.push(`${officials} figure${officials === 1 ? '' : 's'} matching `
            + 'neither kit still counted — a referee, or your goalkeeper');
    }

    // Only once there are keeping figures on screen to caveat, and only when
    // nobody confirmed who they belong to. `pitchiq/keeper.py` is blunt about the
    // stake — a wrong keeper poisons save percentage, distribution and every
    // keeper feature in the xG model at once — and the rows look identical
    // whether a person named the keeper or the pipeline worked them out from
    // kit colour and where they stood. 'manual' is a human saying so and needs
    // no caveat; 'unavailable' is the deliberate refusal to guess, and puts no
    // rows on screen to caveat in the first place.
    const keeperMethod = q.keeper_method ?? q.keeperMethod;
    if (options.keepers && keeperMethod === 'colour+position') {
        notes.push('nobody named the goalkeepers — they were picked out by kit '
            + 'colour and where they stood, and every keeping figure rests on '
            + 'that having been right');
    }

    // Only once there is a line height on screen to caveat, and it needs one
    // badly. "Defensive line height" conventionally means where the back line
    // sat *out of possession*; this is averaged over every instant of the run,
    // including the spells this side spent camped in the opposition half with
    // the ball. Those spells pull it up, so the figure reads higher than the
    // thing a coach means by it, and by more for a side that had most of the
    // ball. The pipeline cannot yet split the run by who was in possession —
    // when it can, this figure changes and this line goes away.
    if (options.lineHeight != null) {
        notes.push('the defensive line is averaged across the whole run, not '
            + 'just the spells out of possession — time spent camped in their '
            + 'half pulls it higher than it sat when defending');
    }

    // Only once there is an xG figure on screen to caveat. Both facts below are
    // biases with a known direction, which is worth more to a coach than a
    // vague warning: headers are scored generously, and a loose calibration
    // widens every shot's number without moving the total much.
    //
    // The 0.5m figure is not a guess. Measured against the real model
    // (tests/test_xg_noise.py): half a metre of position error moves one shot's
    // xG by ~0.030 on a 0.188 baseline, and by 2m the spread exceeds the number
    // itself.
    if (options.shots) {
        const error = options.calibrationErrorM;
        const trust = xgTrust(error);
        const at = error == null ? '' : ` at ${error.toFixed(1)}m of calibration error`;

        if (trust === 'none') {
            notes.push(`xG is not shown${at} — the model moves further than the `
                + 'number is worth when the positions are that loose');
        } else {
            // Named as an assumption with an owner rather than a flat
            // limitation, now that there is something to be done about it. The
            // count is what stops the caveat reading as unfixed on a match
            // where it has been fixed.
            const tagged = options.headersTagged || 0;
            notes.push('xG counts every shot as struck with the foot'
                + (tagged
                    ? ` except the ${tagged} you tagged as `
                        + `${tagged === 1 ? 'a header' : 'headers'}`
                    : ' until somebody says otherwise')
                + ' — one camera cannot see the ball\'s height'
                + (trust === 'total'
                    ? `, and${at} only the total is shown: each shot on its own is `
                        + 'looser than the differences a shot map would draw'
                    : ''));
        }
    }

    const perCluster = q.tracks_per_cluster ?? q.tracksPerCluster;
    if (perCluster > 2) {
        notes.push(
            `tracking broke each player into about ${Math.round(perCluster)} pieces`,
        );
    }

    return notes;
}
