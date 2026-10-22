# Security

PitchIQ stores the names, shirt numbers and email addresses of high school
students, and match statistics about them. The site has no server:
`firestore.rules` is the entire security boundary.

## Reporting a problem

Email **alexvega20137@gmail.com**. Do not open a public issue, and do not test
against real teams' data: use the Firestore emulator (`npm run emulators`,
see [FIREBASE_SETUP.md](FIREBASE_SETUP.md)).

Say what an attacker can do, from which kind of account (signed out, any
Google account, a player, tagging staff, a coach of another team), and how to
reproduce it. A failing case in the shape of `tests/rules.test.js` is the most
useful form.

## What counts

- **A rules gap**: any account reading or changing data it should not. For
  example a player reading another player's report, staff reading students'
  email addresses, or anyone reaching a team they do not belong to. The
  privacy page (`about/`) says who should see what.
- **Exposure of student data** anywhere else: logs, the published site, the
  repository or its history.
- **A way past the coach allowlist** to create a team.

## What does not

- The Firebase web API key in `shared/firebase-init.js`. It is a public
  project identifier by design; the rules protect the data.
- Copies of PitchIQ run by someone else on their own Firebase project. Those
  are their operators' to secure, though a flaw in the rules themselves
  affects every copy and is worth reporting here.

## Supported versions

Only the current `main` branch, which is what the live site runs.
