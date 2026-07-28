# Firebase setup

The app is a static site with no server: the browser talks to Firestore directly
and `firestore.rules` is the entire security boundary. These steps have to be
done by hand in the Firebase console — they can't be scripted from the repo.

This database holds names and email addresses of high school students. Step 3 in
particular is not optional.

---

## 1. Create the project

<https://console.firebase.google.com> → **Add project**.

Google Analytics is optional and adds a data-collection surface you'd have to
disclose. Skip it unless you want it.

## 2. Firestore

**Build → Firestore Database → Create database**

- Mode: **Native**
- Location: pick carefully — **it is permanent and cannot be changed later.**
  `nam5` (US multi-region) or `us-central1` are fine.

## 3. Start in production (locked) mode — never test mode

Test mode makes the database world-readable and world-writable for 30 days.
With real students' data that is not a survivable mistake. Choose locked mode;
the rules in this repo replace the defaults in step 6.

## 4. Authentication

**Build → Authentication → Get started → Sign-in method**

- Enable **Google**. Set the project support email and a public-facing name —
  students see it on the consent screen.
- Explicitly confirm **Anonymous**, **Email/Password**, and **Email link** are
  disabled. The rules require `sign_in_provider == 'google.com'`, so enabling
  another provider later wouldn't grant access, but leaving them off keeps the
  account list clean.

**Authentication → Settings → Authorized domains** → add `<username>.github.io`.
`localhost` is there by default. Without this, sign-in fails on the live site.

## 5. Register the web app and paste the config

**Project settings → Your apps → Web (`</>`)**, then copy the `firebaseConfig`
object into `assets/firebase-init.js`, replacing the `REPLACE_ME` placeholders.

That file is committed and served publicly, which is correct — a web API key is
a project identifier, not a secret. The rules are what protect the data.

> Never commit a **service account** JSON. That bypasses all rules entirely.
> `.gitignore` already excludes the usual filenames.

## 6. Deploy the rules and indexes

```bash
npx firebase login
npx firebase use --add          # pick your project
npm run deploy:rules
```

Keep editing rules in `firestore.rules` in this repo, not in the console — an
untracked console edit is how a correct ruleset quietly becomes a wrong one.

## 7. Add yourself to the coach allowlist

Anyone with a Google account can sign in, but only allowlisted addresses can
create a team. Without this gate, strangers could start entering students' names
and emails into your project, on your quota.

In **Firestore → Data**, create a collection `coachAllowlist` with one document
per coach:

- Document ID: the coach's email, **lowercased** (e.g. `coach@school.org`)
- One field is enough: `note` (string)

Clients can't read or write this collection — the rules check it internally.

## 8. Recommended hardening

- **App Check** (Build → App Check) with reCAPTCHA v3. Run in monitoring mode
  for a week, then enforce on Firestore. This is the main defence against
  someone burning your quota using the public API key.
- **Restrict the browser key**: Google Cloud Console → APIs & Services →
  Credentials → HTTP referrer restriction (`https://<username>.github.io/*`,
  `http://localhost:*`).

---

## Running locally

```bash
npm install
npm run emulators      # Firestore on 8085, Auth on 9099, UI on 4000
```

Then serve the site from the repo root on any static server, e.g.

```bash
python -m http.server 8080
```

`assets/firebase-init.js` auto-detects `localhost` / `127.0.0.1` and connects to
the emulators, so local development never touches production data.

## Running the tests

```bash
npm test
```

Starts a Firestore emulator, runs the rules suite (what must be denied) and the
flow suite (what must work), then shuts down. Java is required — the emulator
runs on the JVM.

---

## First run, in order

1. Sign in on the landing page with an allowlisted account.
2. Create your team.
3. **Roster** tab → add players, each with the Google address they'll sign in
   with. That email is what links a player to their own report.
4. Hit **Invite** on each player.
5. **Matches** tab → create a match.
6. Open **Live tagging**, pick the match, set the lineup, and tag.
7. Back in the coach dashboard, open the match and **Publish player reports**.
8. Players sign in, accept the invitation, and see only their own numbers.
