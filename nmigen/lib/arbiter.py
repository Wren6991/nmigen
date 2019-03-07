from .. import *
from enum import Enum


class ArbiterInterface:
    _doc_template = """
    {description}

    Parameters
    ----------
    n_req : int
        Number of requests to be arbitrated
    {parameters}

    Attributes
    ----------
    i : Signal(n_req), in
        Vector of requests. Each bit represents a request, and is
        1 when that request is asserted.
    o : Signal(n_req), out
        One-hot containing the single granted request. May also be all zeroes,
        in case no request is granted. Driven combinatorially from ``req``;
        request to grant is in-cycle, although the arbiter may register
        state internally.
    {attributes}
    """

    __doc__ = _doc_template.format(description="Common arbiter interface",
        parameters="", attributes="").strip()

    def __init__(self, n_req):
        if not isinstance(n_req, int) or n_req < 1:
            raise TypeError(
                "n_req must be a positive integer, not '{!r}'".format(n_req))
        self.req = Signal(n_req)
        self.gnt = Signal(n_req)

    def elaborate(self, platform):
        raise NotImplementedError  # :nocov:


def _smear_lx(sig):
    return Cat(sig[:i].bool() for i in range(sig.nbits))


def _priority_sel(req):
    deny = _smear_lx(req)
    return req & ~deny


def _rrobin_sel(req, prev_grant):
    req_above_prev = req & _smear_lx(prev_grant)
    req_unwrap = Cat(req_above_prev, req)
    grant_unwrapped = _priority_sel(req_unwrap)
    return grant_unwrap[:req.nbits] | grant_unwrap[req.nbits:]


def _bitmap_mux(sel, data, width):
    return Cat((sel & Cat(data[i][j] for j in range(len(sel))).bool()) for i in range(width))


class PriorityArbiter(ArbiterInterface):
    __doc__ = ArbiterInterface._doc_template.format(
        description="Lowest-numbered (first) request is granted.",
        parameters="", attributes="")

    def __init__(self, n_req):
        super().__init__(self, n_req)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.o.eq(_priority_sel(self.i))

        return m


class RobinPolicy(Enum):
    """
    RobinPolicy.WITHDRAW:
        Once granted, a given request remains granted on every cycle
        until that request is withdrawn, at which point there is a new
        round of arbitration.
    RobinPolicy.CE:
        A new round of arbitration on every cycle where ``en`` is high.
        The currently-granted request will only be re-granted if
        there are no other requests.
    """
    WITHDRAW = 0
    CE = 1


class RoundRobinArbiter(ArbiterInterface):
    __doc__ = ArbiterInterface._doc_template.format(description="""
    Grant each of a set of requests, in turn, in a rotating fashion.

    In each round of arbitration, only requests higher-numbered than
    the most-recently-granted are considered, with priority to the
    lowest-numbered of these. If there are no such requests, the
    lowest-numbered of all requests is chosen.
    """,
    parameters="""
    policy : RobinPolicy
        Policy on when to perform a new round of arbitration.
        See :class:``RobinPolicy``
    """.strip(),
    attributes="""
    en : Signal(1)
        Request enable. Only present if ``policy`` is RobinPolicy.CE.
        A newly-arbitrated grant bitmap is presented on ``o`` on
        cycles where ``en`` is asserted.
        Otherwise, ``o`` does not change.
    """.strip())

    def __init__(self, n_req, policy=RobinPolicy.CE):
        super().__init__(self, n_req)
        if not isinstance(policy, RobinPolicy):
            raise TypeError("policy must be a RobinPolicy, not '{!r}'".format(policy))
        self.policy = policy
        if policy == RobinPolicy.CE:
            self.en = Signal()

    def elaborate(self, platform):
        m = Module()

        grant_prev = Signal(self.n_req)
        grant = _rrobin_sel(self.i, grant_prev)

        if self.policy == RobinPolicy.CE:
            en = self.en
        else:
            en = ~((self.i & grant_prev).bool())

        with m.If(en):
            m.d.sync += grant_prev.eq(grant)
            m.d.comb += self.o.eq(grant)
        with m.Else():
            m.d.sync += self.o.eq(grant_prev)

        return m


class ProgPriorityArbiter(ArbiterInterface):
    __doc__ = ArbiterInterface._doc_template.format(description="""
    The priority of each request can be set dynamically.
    Highest priority wins.
    In case of a tie, grant is arbitrarily given to the
    lowest-numbered request.""",
    parameters="""
    max_pri : int
        The highest priority number that can be assigned
        to a request. Defaults to n_req.""",
    attributes="""
    pri : Array(Signal(max=max_pri) for i in range(n_req))
        The current priority of each request.""")

    def __init__(self, n_req, max_pri=None):
        super().__init__(self, n_req)
        if max_pri is None:
            max_pri = n_req
        if not isinstance(max_pri, int) or max_pri < 1:
            raise TypeError(
                "max_pri must be a positive integer, not '{!r}'".format(max_pri))
        self.pri = Array(Signal(max=max_pri) for i in range(n_req))

    def elaborate(self, platform):
        m = Module()

        pri_decoded = [(1 << p)[:self.max_pri] for p in self.pri]

        req_levelled = []
        for p in range(self.max_pri):
            req_levelled.append(Cat(self.i[r] & pri_decoded[r][p] for r in range(self.n_req)))

        highest_active_level = _priority_sel(level.bool() for level in req_levelled)
        level_muxed = _bitmap_mux(highest_active_level, req_levelled, n_req)
        m.d.comb += self.o.eq(_priority_sel(level_muxed))

        return m


class FairAmongEqualsArbiter(ArbiterInterface):
    __doc__ = ArbiterInterface._doc_template.format(description="""
    The priority of each request can be set dynamically.
    Highest priority wins.
    In case of a tie, the tieing requests are arbitrated in
    a round-robin fashion, cycle-for-cycle.""",
    parameters="""
    max_pri : int
        The highest priority number that can be assigned
        to a request. Defaults to n_req.""",
    attributes="""
    pri : Array(Signal(max=max_pri) for i in range(n_req))
            The current priority of each request.""")

    def __init__(self, n_req, max_pri=None):
        super().__init__(self, n_req)
        if max_pri is None:
            max_pri = n_req
        if not isinstance(max_pri, int) or max_pri < 1:
            raise TypeError(
                "max_pri must be a positive integer, not '{!r}'".format(max_pri))
        self.pri = Array(Signal(max=max_pri) for i in range(n_req))

    def elaborate(self, platform):
        m = Module()

        grant_prev = Signal(self.n_req)
        pri_decoded = [(1 << p)[:self.max_pri] for p in self.pri]

        req_levelled = []
        for p in range(self.max_pri):
            req_levelled.append(Cat(self.i[r] & pri_decoded[r][p] for r in range(self.n_req)))

        highest_active_level = _priority_sel(level.bool() for level in req_levelled)
        level_muxed = _bitmap_mux(highest_active_level, req_levelled, n_req)
        grant = _rrobin_sel(level_muxed, grant_prev)

        m.d.comb += self.o.eq(grant)
        m.d.sync += grant_prev.eq(grant)

        return m