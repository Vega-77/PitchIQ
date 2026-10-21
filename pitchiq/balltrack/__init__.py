"""The ball tracker: candidates, classifier, tracker, pitch registration and 3D flight.

Every script runs as a module, from the folder it reads and writes, with the
repo root on PYTHONPATH (the shell scripts source pyenv.sh, which sets it):

    cd pitchiq/balltrack/homog && python -m pitchiq.balltrack.homog.register_frames <lo> <hi> <tag> 4

README.md says what each script does and in what order the shipping chain runs them.
"""


def script(name):
    """`-m` and the module of tracker script `name` ('detect_candidates', 'homog.vballsb').

    How one script runs another: [sys.executable, '-W', 'ignore'] + script(name) + args.
    """
    return ['-m', '%s.%s' % (__name__, name)]
