// Match video, and the two things the rest of the app asks of it: seek to a
// moment, and say where it is.
//
// The second one is newer and is the reason the review tool used to ask a coach
// to read a time off the player and type it into a box. It could not be asked
// for directly, so it was asked for via a human.
//
// A coach is not going to re-encode a match, so the link they paste is
// whatever the footage already lives behind — almost certainly an unlisted
// YouTube upload, since a 45-minute phone video is several gigabytes and
// YouTube hosts it for free. A direct file link works too when there is
// somewhere to put one.
//
// Those two need completely different code to control. YouTube runs in an
// iframe on another origin, so seeking means posting messages at it; a direct
// file is a <video> element with a `currentTime` you can assign. This module
// hides that behind `mount()` and `seek()` so the player portal never has to
// care which it got.
//
//   const video = mount(host, url);
//   video.seek(742);          // seconds into the video
//   video.onTime((s) => …);   // where it is now, as it plays
//
// URLs are parsed rather than trusted. The security rules already require
// https, but this is what actually decides which element the string becomes,
// and a string that parses as neither is refused rather than being dropped
// into an iframe to see what happens.

const YOUTUBE_HOSTS = new Set([
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'youtu.be', 'www.youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com',
]);

const FILE_PATTERN = /\.(mp4|webm|ogg|ogv|mov|m4v)(\?|#|$)/i;

/**
 * What kind of video a URL points at: 'youtube', 'file', or null.
 *
 * null is a refusal, not a fallback. Guessing wrong means embedding an
 * arbitrary page in an iframe, and there is no version of that worth the
 * convenience.
 */
export function videoKind(url) {
    let parsed;
    try {
        parsed = new URL(url);
    } catch {
        return null;
    }
    if (parsed.protocol !== 'https:') return null;
    if (YOUTUBE_HOSTS.has(parsed.hostname)) return youtubeId(parsed) ? 'youtube' : null;
    if (FILE_PATTERN.test(parsed.pathname)) return 'file';
    return null;
}

/** The eleven-character video id out of any of YouTube's URL shapes. */
export function youtubeId(url) {
    const parsed = typeof url === 'string' ? tryUrl(url) : url;
    if (!parsed) return null;

    if (parsed.hostname.endsWith('youtu.be')) {
        const id = parsed.pathname.slice(1);
        return /^[\w-]{11}$/.test(id) ? id : null;
    }
    // /watch?v=ID, and the /embed/ID and /live/ID forms people also paste.
    const fromQuery = parsed.searchParams.get('v');
    if (fromQuery && /^[\w-]{11}$/.test(fromQuery)) return fromQuery;

    const match = parsed.pathname.match(/\/(?:embed|live|shorts)\/([\w-]{11})/);
    return match ? match[1] : null;
}

function tryUrl(value) {
    try {
        return new URL(value);
    } catch {
        return null;
    }
}

/**
 * The subscriber list both players keep, so neither writes it out again.
 *
 * `at` is the last position heard, and starts as null rather than as 0 — a
 * player that has not said anything yet is not a player at the start of the
 * match, and the difference is what decides whether a playhead is drawn at all.
 */
function positionFeed() {
    const listeners = new Set();
    let at = null;

    return {
        currentTime: () => at,
        emit(seconds) {
            if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return;
            at = Math.max(0, seconds);
            for (const listener of listeners) listener(at);
        },
        /** Subscribe. Returns the unsubscribe, which callers must actually use:
         *  the coach page rebuilds its blocks every time a match is opened. */
        onTime(listener) {
            if (typeof listener !== 'function') return () => {};
            listeners.add(listener);
            if (at !== null) listener(at);
            return () => listeners.delete(listener);
        },
        clear() { listeners.clear(); },
    };
}

/**
 * Put a player in `host` and return a handle with `seek(seconds)`.
 *
 * Returns null when the URL is not something we will embed, so a caller can
 * show the link as plain text instead — which is the right outcome for, say,
 * a Google Drive share link that cannot be controlled programmatically.
 */
export function mount(host, url, { onReady } = {}) {
    const kind = videoKind(url);
    if (!kind) return null;

    host.innerHTML = '';
    return kind === 'youtube'
        ? mountYouTube(host, url, onReady)
        : mountFile(host, url, onReady);
}

function mountFile(host, url, onReady) {
    const el = document.createElement('video');
    el.className = 'match-video';
    el.controls = true;
    el.preload = 'metadata';
    el.src = url;
    host.append(el);

    if (onReady) el.addEventListener('loadedmetadata', onReady, { once: true });

    const feed = positionFeed();
    const report = () => feed.emit(el.currentTime);
    // `timeupdate` fires a few times a second while playing and not at all when
    // paused, which is the right shape. `seeked` and `loadedmetadata` are what
    // make a paused player still say where it was parked.
    for (const name of ['timeupdate', 'seeked', 'loadedmetadata']) {
        el.addEventListener(name, report);
    }

    return {
        kind: 'file',
        element: el,
        seek(seconds) {
            // A seek can arrive before the browser knows how long the video is,
            // in which case setting currentTime is silently dropped.
            const go = () => { el.currentTime = Math.max(0, seconds); el.play?.(); };
            if (el.readyState >= 1) go();
            else el.addEventListener('loadedmetadata', go, { once: true });
        },
        currentTime: feed.currentTime,
        onTime: feed.onTime,
        destroy() {
            for (const name of ['timeupdate', 'seeked', 'loadedmetadata']) {
                el.removeEventListener(name, report);
            }
            feed.clear();
            el.src = '';
            el.remove();
        },
    };
}

const YT_ORIGIN = 'https://www.youtube-nocookie.com';

// How often to re-introduce ourselves, and how long to keep trying. The embed
// answers once its own player has booted, and there is no event that says when
// that is — `load` on the iframe is the document, not the player. Ten seconds
// of asking twice a second is far longer than it takes on a working connection
// and still stops rather than pinging a dead frame for the rest of the session.
const LISTEN_EVERY_MS = 500;
const LISTEN_FOR_MS = 10000;

function mountYouTube(host, url, onReady) {
    const id = youtubeId(url);
    const frame = document.createElement('iframe');
    frame.className = 'match-video';
    // nocookie so a player watching their own highlights is not also being
    // profiled for it. enablejsapi is what makes seeking possible at all.
    frame.src = `https://www.youtube-nocookie.com/embed/${id}?enablejsapi=1&rel=0&playsinline=1`;
    frame.allow = 'accelerometer; encrypted-media; picture-in-picture; fullscreen';
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    host.append(frame);

    let ready = false;
    frame.addEventListener('load', () => { ready = true; onReady?.(); }, { once: true });

    const post = (func, args) => {
        frame.contentWindow?.postMessage(
            JSON.stringify({ event: 'command', func, args }),
            YT_ORIGIN,
        );
    };

    // Where it is, which YouTube will tell us but only if asked.
    //
    // The embed's API runs over postMessage in both directions. Sending
    // `listening` subscribes this page to `infoDelivery` messages carrying
    // `currentTime`, and a page that never sends it never hears anything — the
    // whole reason the review tool used to say "YouTube will not tell a page
    // where it is". It will. It waits to be asked, and until now nobody asked.
    //
    // This is what the official IFrame API script does under the hood; it is
    // done here directly rather than by loading that script, because the only
    // thing wanted out of it is one number and the script is a third-party
    // dependency on every page that shows a video.
    const feed = positionFeed();
    let heard = false;

    const askToListen = () => {
        frame.contentWindow?.postMessage(
            JSON.stringify({ event: 'listening', id, channel: 'widget' }),
            YT_ORIGIN,
        );
    };

    const onMessage = (event) => {
        if (event.origin !== YT_ORIGIN) return;
        // The coach page has two of these on it at once — the match video and
        // the review tool's own player — so an origin check is not enough to
        // know a message is ours. The frame it came from is.
        if (event.source !== frame.contentWindow) return;

        let data;
        try {
            data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        } catch {
            return;
        }
        const seconds = data?.info?.currentTime;
        if (typeof seconds !== 'number') return;
        heard = true;
        feed.emit(seconds);
    };
    window.addEventListener('message', onMessage);

    let asked = 0;
    const pump = setInterval(() => {
        asked += LISTEN_EVERY_MS;
        if (heard || asked >= LISTEN_FOR_MS) {
            clearInterval(pump);
            return;
        }
        askToListen();
    }, LISTEN_EVERY_MS);
    frame.addEventListener('load', askToListen, { once: true });

    return {
        kind: 'youtube',
        element: frame,
        seek(seconds) {
            const go = () => {
                post('seekTo', [Math.max(0, seconds), true]);
                post('playVideo', []);
            };
            if (ready) go();
            else frame.addEventListener('load', go, { once: true });
        },
        currentTime: feed.currentTime,
        onTime: feed.onTime,
        destroy() {
            // Both of these outlive the element they belong to if left alone: a
            // window listener holds the closure, and the interval holds a frame
            // that is no longer in the document. The coach page mounts a new
            // pair of players every time a match is opened.
            clearInterval(pump);
            window.removeEventListener('message', onMessage);
            feed.clear();
            frame.remove();
        },
    };
}

// Where in the video a match-clock moment happened used to live here, as
// `videoTime(clockS, offsetS)`. It is now `matchClockMap(...).toVideo` in
// report.js, because a single offset is only correct until half-time: the
// tablet's clock stops for the break and the footage does not. See the section
// comment there. It moved rather than being wrapped so that there is one
// conversion in the app and not two that agree for forty-five minutes.
