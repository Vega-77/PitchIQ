"""One GPU matcher for the whole detect_candidates pool, instead of one per worker.

Every detect_candidates worker used to build its own gpu_matcher.Matcher, which costs each
process a CUDA context: ~388 MiB of the card and ~1.4 GB of host commit
(a worker commits 2.36 GB with it, 0.91 GB without).  The card is only a
quarter busy (cuda-context notes), so the contexts, not the arithmetic, are
what cap the worker count - 16 workers died of "bad allocation".

Here one server process owns the only context and runs the same
gpu_matcher.Matcher code; workers hold a Proxy that ships each match_many call
over a queue and waits for the answer.  The answer is a pure function of the
descriptors sent, so it is the same arrays whichever process computes it.
Each task gets a fresh Matcher on the server (`start`), exactly as each
worker() call built a fresh one before, and evict() is forwarded in order on
the same queue as the matches it must precede.
"""

import multiprocessing as mp


def serve(req, resps):
    """Server loop: ('start', i) | ('match', i, queries, fb, db) | ('evict', i, oldest) | None."""
    from . import gpu_matcher
    import torch

    torch.set_num_threads(1)
    ms = {}
    for msg in iter(req.get, None):
        op, i = msg[0], msg[1]
        if op == 'start':
            ms[i] = gpu_matcher.Matcher()
        elif op == 'evict':
            ms[i].evict(msg[2])
        else:
            try:
                resps[i].put(('ok', ms[i].match_many(msg[2], msg[3], msg[4])))
            except Exception as e:  # the worker raises it; the server lives on
                resps[i].put(('err', repr(e)))


class Proxy:
    """The two calls pan_registration.Registrar makes on its matcher, answered by the server."""

    def __init__(self, req, resp, i):
        """Worker i's handle on the server: asks it for a fresh Matcher."""
        self.req, self.resp, self.i = req, resp, i
        req.put(('start', i))

    def evict(self, oldest):
        """Forward Matcher.evict(oldest) to the server, in order with the matches."""
        self.req.put(('evict', self.i, oldest))

    def match_many(self, queries, fb, db):
        """Matcher.match_many(queries, fb, db) run by the server; raises if the server's call
        failed.
        """
        if not queries:
            return []
        self.req.put(('match', self.i, queries, fb, db))
        st, out = self.resp.get()
        if st != 'ok':
            raise RuntimeError('shared matcher: ' + out)
        return out


_W = {}


def init_worker(req, resps, slot):
    """Pool initializer: this process's slot, i.e. which response queue is its own."""
    with slot.get_lock():
        i = slot.value
        slot.value += 1
    _W.update(req=req, resp=resps[i], i=i)


def proxy():
    """This pool worker's Proxy (after init_worker)."""
    return Proxy(_W['req'], _W['resp'], _W['i'])


def start(procs):
    """The server and the pool's initializer arguments; stop(server, req) when done."""
    ctx = mp.get_context('spawn')
    req = ctx.Queue()
    resps = [ctx.Queue() for _ in range(procs)]
    server = ctx.Process(target=serve, args=(req, resps), daemon=True)
    server.start()
    return server, req, (req, resps, ctx.Value('i', 0))


def stop(server, req):
    """Tell the server to finish and wait up to 60 s for it."""
    req.put(None)
    server.join(60)
