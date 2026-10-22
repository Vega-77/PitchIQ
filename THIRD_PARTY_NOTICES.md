# Third-party notices

PitchIQ's own [LICENSE](LICENSE) (PolyForm Noncommercial 1.0.0) covers what is
written for this repo. It does **not** apply to anything below: each keeps its
own license, and that license governs your use of it. None of the Python
packages or model weights below are stored in this repo; pip, the README
Setup steps or the browser fetch them.

The Python licenses were read from each installed package's metadata
(`importlib.metadata`) at the pinned versions, then checked by hand against
the projects' own pages. This file states facts about licenses; it is not
legal advice.

## Python packages

| Package | Version | License | Installed by | Used for |
|---|---|---|---|---|
| [Ultralytics](https://github.com/ultralytics/ultralytics) | 8.4.108 | AGPL-3.0 | `[detect]` extra, `requirements.txt` | person and ball detector, player trackers (`ultralytics.trackers`) |
| ultralytics-thop | 2.1.0 | AGPL-3.0 | with Ultralytics | Ultralytics' own dependency |
| [PyTorch](https://github.com/pytorch/pytorch) (`torch`) | 2.13.0 | BSD-3-Clause style (bundled parts Apache-2.0, BSD-2-Clause and others) | `[detect]` extra | runs the detector and TAPNext++ |
| torchvision | 0.28.0 | BSD-3-Clause | `[detect]` extra | image ops for the detector |
| [OpenCV](https://github.com/opencv/opencv-python) (`opencv-python`) | 5.0.0.93 | Apache-2.0 | always | video, homographies, drawing |
| [NumPy](https://numpy.org) | 2.4.4 | BSD-3-Clause (bundled parts 0BSD, MIT, Zlib, CC0-1.0) | always | arrays |
| [SciPy](https://scipy.org) | 1.18.0 | BSD-3-Clause | always | optimisation, assignment, filters |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 1.28.0 | MIT | always | runs the xG model |
| [NetworkX](https://networkx.org) | 3.6.1 | BSD-3-Clause | always | the top-down map's min-cost flow (`topdown.py`) |
| [XGBoost](https://github.com/dmlc/xgboost) | 3.4.0 | Apache-2.0 | `[balltrack]`, `[xg-training]` | ball candidate ranker, xG training |
| matplotlib | 3.11.1 | Matplotlib License (PSF-style) | `[balltrack]`, `[xg-training]` | plots |
| Pillow | 12.2.0 | MIT-CMU | `[balltrack]` | images |
| einops | 0.8.2 | MIT | `[balltrack]` | TAPNext++ |
| statsbombpy | 1.22.0 | none declared (no license in its PyPI metadata or its GitHub repo) | `[xg-training]` | downloads StatsBomb open data |
| scikit-learn | 1.9.0 | BSD-3-Clause | `[xg-training]` | xG model evaluation |
| pandas | 3.0.5 | BSD-3-Clause | `[xg-training]` | xG training tables |
| onnxmltools | 1.16.0 | Apache-2.0 | `[xg-training]` | exports the xG model to ONNX |
| pytest, pyflakes, ruff | 9.1.1, 3.4.0, 0.17.1 | MIT | `[test]` | tests and linting only |

### Ultralytics (AGPL-3.0)

`pip install -e .[detect]` (or `pip install -r requirements.txt`) installs
Ultralytics, which is licensed under the
[GNU Affero General Public License v3.0](https://www.ultralytics.com/license).
Ultralytics states that using its code or models in a project requires
either releasing that project under AGPL-3.0 or buying an Ultralytics
Enterprise License. The detector weights `yolov8n.pt` that the README tells
you to download come from Ultralytics and are AGPL-3.0 as well.

PitchIQ does not include Ultralytics or its weights; you install them
yourself, and they are needed only for the detector and the trackers. Code
that does not run the detector (the report, xG and site code, and the test
suite) runs without them.

The owner's decision (2026-10-21): keep PitchIQ's license, state this here
and in the README, and treat a permissively licensed detector and tracker as
a roadmap item. If a school district, club or company plans to adopt
PitchIQ, it should take its own legal advice on this combination first.

## Ball tracker

| Component | License | Where it comes from |
|---|---|---|
| [tapnet](https://github.com/google-deepmind/tapnet) code (commit 730cda1) | Apache-2.0 | cloned into `pitchiq/balltrack/tapnext_bridge/tapnet` (not in git) |
| TAPNext++ checkpoint `tapnextpp_512.ckpt` | Apache-2.0 (tapnet's README: all released checkpoints) | downloaded per `requirements-balltrack.txt` (not in git) |

## Data

| Data | Terms | Used for |
|---|---|---|
| [StatsBomb Open Data](https://github.com/statsbomb/open-data) | StatsBomb Public Data User Agreement (`LICENSE.pdf` in that repo). Anyone publishing work based on it is asked to name StatsBomb as the source and use their logo. | training the xG model, `pitchiq/xg_model8.onnx` |

The xG model in this repo was trained on StatsBomb open data. The data
itself is not in the repo.

## Website

| Component | License | How the site uses it |
|---|---|---|
| [Firebase JavaScript SDK](https://github.com/firebase/firebase-js-sdk) 12.16.0 | Apache-2.0 | loaded by the browser from `www.gstatic.com` (sign-in, Firestore) |
| Fonts: Graduate, Saira, Saira Condensed | SIL Open Font License 1.1 | from Google Fonts |

## Development tools (npm, not shipped)

| Package | License | Used for |
|---|---|---|
| firebase, @firebase/rules-unit-testing, firebase-tools | Apache-2.0, Apache-2.0, MIT | the Firestore emulator and rules tests (`npm test`) |
