# Frontend notes

## Cache busting

Run this before committing any frontend change, and bump the number each time:

```bash
python stamp_version.py 6
```

There is no build step and therefore no hashed filenames, so without a version
query browsers happily keep serving the old file — GitHub Pages sets a
ten-minute cache, and a dev server sets none at all but browsers cache
heuristically anyway. A stylesheet that is correct on disk, correct on the wire,
and stale in the browser looks exactly like a bug in your CSS, which is why this
is scripted rather than left to memory.

The script stamps **two** things, and the second is the one that matters:

```html
<link rel="stylesheet" href="../assets/app.css?v=5">
<script type="module" src="coach.js?v=5"></script>
```

```js
import { byId, toast } from '../assets/ui.js?v=5';
```

Versioning only the page's `<link>` and `<script>` tags is half a fix, because a
versioned entry point does **not** version what it imports: `coach.js?v=5` loads
fresh and then pulls `../assets/auth.js` straight from cache. That failure mode
is genuinely misleading — it surfaces as

```
SyntaxError: The requested module '../assets/auth.js'
does not provide an export named 'saveStaffProfile'
```

which reads like a missing export in code that is actually correct on disk. It
cost debugging time twice before the imports were versioned too.

One consequence worth knowing: if you poke at the app from the browser console,
`import('/assets/firebase-init.js')` and `import('/assets/firebase-init.js?v=5')`
are **different module instances**, and the second one to initialise Firebase
throws. Use the same specifier the page used.

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

Three rules worth keeping:

- **`assets/ui.js` before a private helper.** Toasts, `byId`, view switching and
  the stat/figure builders each used to exist as a private copy in four or five
  page scripts. If you need one of those again, import it.
- **`assets/events.js` owns what an event means.** `EVENTS` is the map of
  specs; `EVENT_TYPES` is its key list. `db.js` used to declare a second,
  unrelated `EVENT_TYPES`, so the same name meant two different things
  depending on which module you had imported.
- **Never assume one team.** A coach may hold several squads (varsity and JV),
  and a squad may have several coaches. Anything reading `access.teams[0]` is a
  bug: live tagging used to do exactly that, which would have recorded a JV
  match against the varsity roster without saying a word.

## Squads and staff

Authority lives in two places and nowhere else:

- `teams/{t}.coachUids` — who coaches this squad.
- `teams/{t}/players/{p}.linkedUid` — which account is this player.

`teams/{t}/staff/{uid}` is **display only**: a name and address for each uid, so
the staff list is not a column of opaque ids. It is self-written and the address
must match the verified token, so it cannot be used to misattribute anyone — and
because `listStaff()` iterates `coachUids` rather than the directory, a forged or
missing entry changes what you see but never who has access.

Adding a coach is invite-then-claim, like adding a player, with one deliberate
difference: a player claim re-verifies against the roster document's own stored
email and ignores the invite, whereas for a coach **the invite is the grant**.
There is no second document to check against. That is safe because only an
existing coach of that squad can write the invite, it is keyed by the invitee's
address, and claiming still needs a verified Google token for that address.

Two guards worth not removing:

- Only the coach who **created** a squad can remove another coach, so an
  assistant cannot lock the head coach out.
- The claim rule requires the new `coachUids` to equal the old array with
  exactly the claimant's uid appended, so it cannot rename the squad, drop a
  colleague, or add a third party.

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
