<!-- What this changes and why, in a sentence or two. One change per pull
     request: a behaviour change and a refactor go in separate ones. -->


## Checks

- [ ] `python -m pytest tests/ -q`
- [ ] `python -m pyflakes pitchiq/ tests/`
- [ ] `python -m ruff format --check pitchiq tests tools xg_training`
- [ ] `python -m ruff check pitchiq tests tools xg_training`
- [ ] `npm test`, if JS, HTML, CSS or `firestore.rules` changed
- [ ] `python stamp_version.py <n+1>`, if any file the site serves changed
- [ ] A refactor shows it changes nothing: same test results, same
      `report.to_json()`, byte-identical ball-tracker output where it touches
      `pitchiq/balltrack/`
- [ ] A `firestore.rules` change has tests for what must now be denied
- [ ] No video, frames or students' names or addresses in the diff or here

See [CONTRIBUTING.md](https://github.com/Vega-77/PitchIQ/blob/main/CONTRIBUTING.md).
