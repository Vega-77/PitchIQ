# Frontend notes

## Cache busting

Every local stylesheet and entry-point script is referenced with `?v=N`, e.g.

```html
<link rel="stylesheet" href="../assets/app.css?v=3">
<script type="module" src="coach.js?v=3"></script>
```

**Bump that number whenever you ship a CSS or JS change**, in every page that
references the changed file. There is no build step and therefore no hashed
filenames, so without it browsers happily keep serving the old file — GitHub
Pages sets a ten-minute cache, and a dev server sets none at all but browsers
cache heuristically anyway. This cost real debugging time three separate times
during development: a stylesheet that was correct on disk, correct on the wire,
and stale in the browser looks exactly like a bug in your CSS.

### The gap this does not close

A versioned entry point does **not** version what it imports. When
`landing.js?v=3` loads fresh and imports `./ui.js`, that import has no query
string and may still come from cache.

In practice bumping the version is enough for most changes, because the file
being edited is usually a stylesheet or an entry point. When you have edited a
shared module — anything in `assets/` imported by several pages — and the change
does not appear, hard-refresh (Ctrl+Shift+R) rather than assuming it did not
deploy.

## Layout of the frontend

Each page directory is named after what it is, and its files are named after
the directory — `coach/coach.js`, `player/player.js`, `live-tagging/tagging.js`.
Shared code lives in `assets/` and is imported by path.

```
assets/            shared by every page
  app.css            design system: tokens, type scale, components
  firebase-init.js   SDK setup   <- your Firebase config goes here
  auth.js            sign-in, roster claim, role resolution
  db.js              every Firestore read and write
  events.js          what each match event means, in one place
  ui.js              DOM helpers: toast, view switching, stat cards, timeline rows
  pitch-backdrop.js  the faint pitch diagram behind page headers
  landing.js/.css    the site root, which is also the signed-in home

index.html         landing page and sign-in
coach/             dashboard: season, roster, matches, player reports
player/            portal: a player's own reports only
live-tagging/      match-day tablet tool
calibrate/         camera calibration
xg-sandbox/        the manual xG model, with the ONNX file it loads
```

Two rules worth keeping:

- **`assets/ui.js` before a private helper.** Toasts, `byId`, view switching and
  the stat/figure builders each used to exist as a private copy in four or five
  page scripts. If you need one of those again, import it.
- **`assets/events.js` owns what an event means.** `EVENTS` is the map of
  specs; `EVENT_TYPES` is its key list. `db.js` used to declare a second,
  unrelated `EVENT_TYPES`, so the same name meant two different things
  depending on which module you had imported.

## Local development

```bash
PitchIQHelper/.venv/Scripts/python.exe -m http.server 8080
```

Then open **`http://localhost:8080/?prod=1`**.

Two details worth knowing:

- **`?prod=1`** points the app at the live Firebase project. Without it, local
  development connects to the emulators, which is the safe default but looks
  broken if they are not running. `?emu=1` switches back. The choice sticks for
  the tab session.
- **`localhost`, not `127.0.0.1`.** Only `localhost` is in the project's
  Firebase authorized domains, so Google sign-in fails on the IP form with an
  `auth/unauthorized-domain` error that does not obviously explain itself.
