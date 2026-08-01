// Match video, and the one thing the rest of the app asks of it: seek to a
// moment.
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
        destroy() { el.src = ''; el.remove(); },
    };
}

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
            'https://www.youtube-nocookie.com',
        );
    };

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
        destroy() { frame.remove(); },
    };
}

/**
 * Where in the video a match-clock moment happened.
 *
 * The clock starts at kick-off; the recording almost never does. Without the
 * offset every marker on the timeline lands at the wrong moment — usually
 * during the warm-up — which looks like the whole feature is broken rather
 * than like one number is unset.
 */
export function videoTime(matchClockS, offsetS = 0) {
    return Math.max(0, (matchClockS || 0) + (offsetS || 0));
}
