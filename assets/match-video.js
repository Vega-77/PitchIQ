// The match video with moments marked along it, wherever that is needed.
//
// Three pages want this now: a player looking at their own touches, a coach
// looking at the whole match, and the half-time view. They differ only in which
// marks go on the strip and what the sentence above it says — everything else
// is the same three-way decision:
//
//   - the link is something we can embed  -> a player, and the marks seek it;
//   - the link is something we will not embed (a Drive share, say) -> the link
//     as a link, and the marks become clock readings;
//   - there is no link -> just the readings.
//
// That decision was written out once in player.js and would have been written
// twice more. It matters that it stays one thing, because the middle case is
// the one that is easy to get wrong: a URL we refuse is not a broken URL, and
// dropping it silently would lose the coach's footage rather than declining to
// frame it.

import { mount, videoKind } from './video.js?v=84';
import { renderStrip, renderMomentList, timelineEnd } from './timeline.js?v=84';
import { matchClockMap } from './report.js?v=84';

/**
 * What we can do with a link: 'embed', 'link' or 'none'.
 *
 * Separated from the rendering so the wording around it can be chosen by the
 * caller and checked by a test without a DOM. `videoKind` answers a narrower
 * question — which element to build — and returns null both for "nothing here"
 * and for "we will not embed this", which are different things to say to a
 * coach who has just pasted something.
 */
export function videoPlacement(url) {
    if (!url) return 'none';
    return videoKind(url) ? 'embed' : 'link';
}

/**
 * Put the video and its marks into the given hosts.
 *
 * `hosts` is `{ video, strip, list, note }`; any of them may be absent, so a
 * page that wants the strip without the list does not have to invent an
 * element to throw away. `notes` carries the sentence for each placement, since
 * "ask your coach to add one" and "paste a link above" are the same fact told
 * to two different people.
 *
 * Returns the video handle, or null. The caller owns it and must `destroy()` it
 * when leaving — an iframe removed from view keeps playing, and a page that is
 * still talking after you have navigated away has no obvious off switch.
 *
 * `onClock` is called with `{ videoS, clockS, period }` as the video plays,
 * for a caller that wants to say where it is in words. The playhead on the
 * strip and the lit row in the list happen here whether or not anyone asks.
 */
export function renderMatchVideo(hosts, options) {
    const {
        url, marks = [], notes = {}, emptyText, clockText,
        halfS, endFloorS, extraTimes = [],
    } = options;

    // `clock` is the whole map, not just the kick-off offset, because seeking a
    // second-half mark with the offset alone lands in the middle of half-time.
    // An `offsetS` on its own is still accepted and still means what it meant.
    const clock = options.clock
        || matchClockMap({ videoOffsetS: options.offsetS ?? 0 });

    const placement = videoPlacement(url);
    let handle = null;

    if (hosts.video) {
        hosts.video.innerHTML = '';
        // A frame holding one button should not be a 16:9 black box. See
        // `.video-frame.is-link` — without this the fallback reserves the whole
        // height a video would have taken and puts a small link in the middle
        // of it, which reads as a video that failed to load rather than as a
        // link we chose not to frame.
        hosts.video.classList.toggle('is-link', placement === 'link');

        if (placement === 'embed') {
            handle = mount(hosts.video, url);
        } else if (placement === 'link') {
            // Still worth handing over, just not as something we can seek.
            const link = document.createElement('a');
            link.className = 'btn small';
            link.href = url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = 'Open the match video';
            hosts.video.append(link);
        }
    }

    if (hosts.note) hosts.note.textContent = notes[placement] || '';

    // Seeking is only meaningful against a player we control. Without one
    // `onSeek` stays undefined and `renderStrip` does nothing on click.
    //
    // A caller may take it over — the player portal does, because it wants to
    // say "no playable video for this match yet" out loud rather than have a
    // tap quietly do nothing, and it wants to scroll the video into view. Its
    // handler closes over its own handle, so it is passed rather than derived.
    const onSeek = options.onSeek
        || (handle ? (clockS) => handle.seek(clock.toVideo(clockS)) : undefined);

    // The strip runs to the end of the match, or later if anything was tagged
    // past it, so a mark at 94 minutes does not fall off the end of the bar.
    const endS = timelineEnd(
        [...marks, ...extraTimes.map((clockS) => ({ clockS }))],
        endFloorS,
    );

    const strip = hosts.strip
        ? renderStrip(hosts.strip, { marks, endS, onSeek, clockText, halfS })
        : null;
    const list = hosts.list
        ? renderMomentList(hosts.list, { marks, onSeek, emptyText, clockText })
        : null;

    if (!handle) return null;

    // The other direction. Everything above this line is the page telling the
    // video where to go; this is the video telling the page where it got to,
    // which it will only do for a player we control — a link we declined to
    // embed has no position to report, and neither has an absent one.
    const stop = handle.onTime((videoS) => {
        const { clockS, period } = clock.toClock(videoS);
        strip?.setNow(clockS);
        list?.setNow(clockS);
        options.onClock?.({ videoS, clockS, period });
    });

    return {
        ...handle,
        destroy() { stop(); handle.destroy(); },
    };
}

// Which tagged entries are worth a mark on a whole-team strip.
//
// Not everything. A tagged half carries thirty restarts and twenty fouls, and a
// strip with eighty ticks on it is a texture rather than a set of things to
// jump to. Goals, cards and substitutions are the moments a coach actually
// scrubs back to; the rest are the raw material for the counts elsewhere on the
// page, and they stay there.
const TEAM_MARK_TYPES = new Set(['goal', 'card']);

/**
 * The team's markable moments out of a tagged log.
 *
 * `describe` is `describeEvent` from events.js, passed in rather than imported
 * so this module stays free of the label vocabulary — the coach page and the
 * half-time page name the two teams differently, and that naming belongs to
 * them.
 */
export function teamMarks(log, describe) {
    const marks = [];

    for (const entry of log || []) {
        const clockS = entry.matchClockS;
        if (typeof clockS !== 'number') continue;

        // Period boundaries are landmarks, not moments — kick-off and half-time
        // are already drawn on the strip, and a coach does not seek to them.
        if (entry.kind === 'period') continue;

        if (entry.kind === 'sub' || TEAM_MARK_TYPES.has(entry.type)) {
            marks.push({
                clockS,
                // 'sub' and 'card' are the class names the strip and the list
                // already colour; see `.tick` and `.moment` in app.css.
                type: entry.kind === 'sub' ? 'sub' : entry.type,
                label: describe(entry),
            });
        }
    }

    return marks;
}
