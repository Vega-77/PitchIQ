# docs/design/

One page per `pitchiq/` module, named after it (`touches.md` for
`pitchiq/touches.py`). Each holds what the module's docstring and comments no
longer do: why it works the way it does, the measurements behind its
thresholds, and dated history. The docstring says what the code does, and its
last line points here.

The text was moved verbatim in the deep clean (task C-2). When a module
changes, the page records why. Nothing in it is read by code.
