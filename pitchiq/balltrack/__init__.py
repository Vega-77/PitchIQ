"""The ball tracker: candidates, classifier, tracker, pitch registration and 3D flight.

Every script runs as a module, from the folder it reads and writes, with the
repo root on PYTHONPATH (the shell scripts source pyenv.sh, which sets it):

    cd pitchiq/balltrack/pitch_registration && python -m pitchiq.balltrack.pitch_registration.register_frames <lo> <hi> <tag> 4

README.md says what each script does and in what order the shipping chain runs them.
"""


def script(name):
    """`-m` and the module of tracker script `name` ('detect_candidates', 'pitch_registration.ball_on_pitch').

    How one script runs another: [sys.executable, '-W', 'ignore'] + script(name) + args.
    """
    return ['-m', '%s.%s' % (__name__, name)]
