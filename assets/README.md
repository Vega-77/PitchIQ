# Frontend notes

## Cache busting

Every local stylesheet and entry-point script is referenced with `?v=N`, e.g.

```html
<link rel="stylesheet" href="../assets/app.css?v=2">
<script type="module" src="coach.js?v=2"></script>
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
`landing.js?v=2` loads fresh and imports `./pitch-backdrop.js`, that import has
no query string and may still come from cache.

In practice bumping the version is enough for most changes, because the file
being edited is usually a stylesheet or an entry point. When you have edited a
shared module — anything in `assets/` imported by several pages — and the change
does not appear, hard-refresh (Ctrl+Shift+R) rather than assuming it did not
deploy.

## Layout of the frontend

```
assets/            shared by every signed-in surface
  app.css            design system: tokens, type scale, components
  firebase-init.js   SDK setup   <- your Firebase config goes here
  auth.js            sign-in, roster claim, role resolution
  db.js              every Firestore read and write
  events.js          what each match event means, in one place
  pitch-backdrop.js  the faint pitch diagram behind page headers

index.html         landing page and sign-in
coach/             dashboard: season, roster, matches, player reports
player/            portal: a player's own reports only
live-tagging/      match-day tablet tool
calibrate/         camera calibration
demo/              the original manual xG sandbox
```

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
