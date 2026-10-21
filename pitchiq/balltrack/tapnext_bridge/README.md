# balltrack/tapnext_bridge/

TAPNext++ (Google DeepMind) for Hybrid 90's gap bridges.

- `tn_engine.py bridges <tag>` tracks every gap between two confident picks
  (`../evaluation/tn/bridges_<tag>.npy`); `alone <tag>` runs TAPNext on its own.
- `hyb_video.py` and `tn_video.py` render review videos of one window.

Needs a CUDA GPU, the tapnet code cloned to `tapnet/` and the 2.4 GB
checkpoint `tapnextpp_512.ckpt` here; neither is in git
(`requirements-balltrack.txt` says how to get them).
