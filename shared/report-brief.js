// The match in brief.
//
// Part of `report.js`, which re-exports it.

import { COUNT } from './report-teams.js?v=149';

// ------------------------------------------------------------- the match in brief

/** How far apart two figures have to be, as a share of the larger, before the
 *  brief calls it an edge. A quarter: 6 shots to 4 is one, 44 duels to 44 or
 *  72% to 64% accuracy is not. */
const BRIEF_EDGE = 0.25;
/** And how big the larger has to be. One corner to none is a 100% gap and
 *  no story at all. */
const BRIEF_MIN_COUNT = 4;

/**
 * The match in four or five lines, for the top of the review.
 *
 * The review runs to about five screens of figures, and a coach opening it the
 * evening after wants the answer before the evidence: the result and who
 * scored, who was booked, where the game was won and lost. Every line here is
 * a figure that is also further down the page; this only picks which ones.
 *
 * `rows` are the team rows the page already draws (`taggedTeamRows` and
 * `teamStatRows`), so an edge here can never quote a figure the page does not
 * show. Goals and cards are left out of the edges: they have lines of their
 * own. Returns `[{ title, detail, tone }]`; empty when nothing was tagged and
 * nothing filmed, and then the caller draws nothing.
 */
export function matchBrief({ log = [], roster = [], counts = null, status = null,
                             rows = [], reads = [] } = {}) {
    const lines = [];
    const events = (log || []).filter((e) => e.kind === 'event');
    const nameOf = new Map((roster || []).map((p) => [p.id, p.playerName]));
    const minute = (e) => `${Math.floor((e.matchClockS || 0) / 60) + 1}′`;
    const who = (e) => nameOf.get(e.playerId) || null;

    if ((log || []).length && counts) {
        const us = counts.us?.goal ?? 0;
        const them = counts.them?.goal ?? 0;
        const score = `${us}–${them}`;
        const title = status === 'full_time'
            ? `${us > them ? 'Won' : us < them ? 'Lost' : 'Drew'} ${score}`
            : `${us > them ? `${score} up` : us < them ? `${score} down` : `Level at ${score}`}`
                + (status === 'halftime' ? ' at half-time' : ' so far');

        const ours = events.filter((e) => e.type === 'goal' && e.side !== 'them');
        const named = ours.map((e) => `${who(e) || 'Unnamed scorer'} ${minute(e)}`);
        lines.push({
            title,
            detail: named.length
                ? `Scored by ${named.join(', ')}.`
                : (us ? '' : 'Nothing scored by your side.'),
            tone: us > them ? 'good' : us < them ? 'bad' : '',
        });

        const cards = events.filter((e) => e.type === 'card' && e.side !== 'them');
        if (cards.length) {
            const red = cards.some((e) => e.cardColor === 'red' || e.cardColor === 'second_yellow');
            lines.push({
                title: cards.length === 1 ? 'One card' : `${cards.length} cards`,
                detail: cards.map((e) => `${who(e) || 'Unnamed player'} ${minute(e)}`
                    + (e.cardColor === 'red' ? ' (red)'
                        : e.cardColor === 'second_yellow' ? ' (second yellow)' : ''))
                    .join(', ') + '.',
                tone: red ? 'bad' : '',
            });
        }
    }

    // Edges: a figure with a better direction, both sides measured, and a gap
    // worth a sentence. Ranked by the gap, so the two named are the biggest.
    const edges = (rows || [])
        .filter((r) => r.better && r.usN != null && r.themN != null)
        .filter((r) => !/^(Goals|Cards)$/.test(r.label))
        .map((r) => {
            const top = Math.max(Math.abs(r.usN), Math.abs(r.themN));
            const gap = top ? (r.usN - r.themN) / top : 0;
            const sized = r.kind !== COUNT || top >= BRIEF_MIN_COUNT;
            return { r, sized, ahead: r.better === 'low' ? -gap : gap };
        })
        .filter((e) => e.sized && Math.abs(e.ahead) >= BRIEF_EDGE)
        .sort((a, b) => Math.abs(b.ahead) - Math.abs(a.ahead));

    const said = (list) => list.slice(0, 3)
        .map(({ r }) => `${r.label} ${r.value ?? r.usN} to ${r.themValue ?? r.themN}`)
        .join(' · ');
    const estimated = (list) => list.slice(0, 3).some(({ r }) => r.confidence);

    const ahead = edges.filter((e) => e.ahead > 0);
    const behind = edges.filter((e) => e.ahead < 0);
    if (ahead.length) {
        lines.push({ title: 'Ahead on', detail: said(ahead), tone: 'good',
                     estimated: estimated(ahead) });
    }
    if (behind.length) {
        lines.push({ title: 'Behind on', detail: said(behind), tone: 'bad',
                     estimated: estimated(behind) });
    }

    // The video's sentences, two at most. They are the reads the rest of the
    // page can only show as figures.
    for (const read of (reads || []).slice(0, 2)) {
        lines.push({ title: read.title, detail: read.detail, tone: '', estimated: true });
    }

    return lines;
}
