"""Encoders and decoders between binary and one-hot representation."""

from .. import *
from enum import Enum

__all__ = [
    "Encoder", "Decoder",
    "PriorityEncoder", "PriorityDecoder",
    "PrioritySelector",
    "GrayEncoder", "GrayDecoder",
    "RobinPolicy",
    "RoundRobinSelector", "RoundRobinEncoder"
]


class Encoder:
    """Encode one-hot to binary.

    If one bit in ``i`` is asserted, ``n`` is low and ``o`` indicates the asserted bit.
    Otherwise, ``n`` is high and ``o`` is ``0``.

    Parameters
    ----------
    width : int
        Bit width of the input

    Attributes
    ----------
    i : Signal(width), in
        One-hot input.
    o : Signal(max=width), out
        Encoded binary.
    n : Signal, out
        Invalid: either none or multiple input bits are asserted.
    """
    def __init__(self, width):
        self.width = width

        self.i = Signal(width)
        self.o = Signal(max=max(2, width))
        self.n = Signal()

    def elaborate(self, platform):
        m = Module()
        with m.Switch(self.i):
            for j in range(self.width):
                with m.Case(1 << j):
                    m.d.comb += self.o.eq(j)
            with m.Case():
                m.d.comb += self.n.eq(1)
        return m


class PriorityEncoder:
    """Priority encode requests to binary.

    If any bit in ``i`` is asserted, ``n`` is low and ``o`` indicates the least significant
    asserted bit.
    Otherwise, ``n`` is high and ``o`` is ``0``.

    Parameters
    ----------
    width : int
        Bit width of the input.

    Attributes
    ----------
    i : Signal(width), in
        Input requests.
    o : Signal(max=width), out
        Encoded binary.
    n : Signal, out
        Invalid: no input bits are asserted.
    """
    def __init__(self, width):
        self.width = width

        self.i = Signal(width)
        self.o = Signal(max=max(2, width))
        self.n = Signal()

    def elaborate(self, platform):
        m = Module()
        for j in reversed(range(self.width)):
            with m.If(self.i[j]):
                m.d.comb += self.o.eq(j)
        m.d.comb += self.n.eq(self.i == 0)
        return m


# Exclusive left smear
def _smear_lx(signal):
    return Cat(signal[:i].bool() for i in range(signal.nbits))

class PrioritySelector:
        """Input a bitmap, ``i`` with any number of bits set.
        Output a bitmap, ``o``, with only the least significant of these set.
        If ``i`` is all zeroes then so is ``o``.

        Parameters
        ----------
        width : int
            Bit width.

        Attributes
        ----------
        i : Signal(width), in
            Input request bitmap
        o : Signal(width), out
            Output grant bitmap
        """
        def __init__(self, width):
            self.i = Signal(width)
            self.o = Signal(width)
            self.width = width

        def elaborate(self, platform):
            m = Module()

            deny = _smear_lx(self.i)
            m.d.comb += self.o.eq(self.i & ~deny)

            return m

class Decoder:
    """Decode binary to one-hot.

    If ``n`` is low, only the ``i``th bit in ``o`` is asserted.
    If ``n`` is high, ``o`` is ``0``.

    Parameters
    ----------
    width : int
        Bit width of the output.

    Attributes
    ----------
    i : Signal(max=width), in
        Input binary.
    o : Signal(width), out
        Decoded one-hot.
    n : Signal, in
        Invalid, no output bits are to be asserted.
    """
    def __init__(self, width):
        self.width = width

        self.i = Signal(max=max(2, width))
        self.n = Signal()
        self.o = Signal(width)

    def elaborate(self, platform):
        m = Module()
        with m.Switch(self.i):
            for j in range(len(self.o)):
                with m.Case(j):
                    m.d.comb += self.o.eq(1 << j)
        with m.If(self.n):
            m.d.comb += self.o.eq(0)
        return m


class PriorityDecoder(Decoder):
    """Decode binary to priority request.

    Identical to :class:`Decoder`.
    """


class GrayEncoder:
    """Encode binary to Gray code.

    Parameters
    ----------
    width : int
        Bit width.

    Attributes
    ----------
    i : Signal(width), in
        Input natural binary.
    o : Signal(width), out
        Encoded Gray code.
    """
    def __init__(self, width):
        self.width = width

        self.i = Signal(width)
        self.o = Signal(width)

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.o.eq(self.i ^ self.i[1:])
        return m


class GrayDecoder:
    """Decode Gray code to binary.

    Parameters
    ----------
    width : int
        Bit width.

    Attributes
    ----------
    i : Signal(width), in
        Input Gray code.
    o : Signal(width), out
        Decoded natural binary.
    """
    def __init__(self, width):
        self.width = width

        self.i = Signal(width)
        self.o = Signal(width)

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.o[-1].eq(self.i[-1])
        for i in reversed(range(self.width - 1)):
            m.d.comb += self.o[i].eq(self.o[i + 1] ^ self.i[i])
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
    CE       = 1


class RoundRobinSelector:
    """Grant each of a set of requests, in turn, in a rotating fashion.

    In each round of arbitration, only requests higher-numbered than
    the most-recently-granted are considered, with priority to the
    lowest-numbered of these. If there are no such requests, the
    lowest-numbered of all requests is chosen.

    Parameters
    ----------
    n_reqs : int
        Number of simultaneous requests
    policy : RobinPolicy
        Policy on when to perform a new round of arbitration.
        See :class:``RobinPolicy``

    Attributes
    ----------
    i : Signal(n_reqs)
        Input request bitmap.
    o : Signal(n_reqs)
        Output grant bitmap.
        There is no register on the path between ``i`` and ``o``;
        requests can be presented and granted in-cycle.
    en : Signal(1)
        Request enable. Only present if ``policy`` is RobinPolicy.CE.
        A newly-arbitrated grant bitmap is presented on ``o`` on
        cycles where ``en`` is asserted.
        Otherwise, ``o`` does not change.
    """
    def __init__(self, n_reqs, policy=RobinPolicy.CE):
        if not isinstance(policy, RobinPolicy):
            raise TypeError("policy must be a RobinPolicy, not '{!r}'".format(policy))
        if not isinstance(n_reqs, int) or n_reqs < 1:
            raise TypeError("n_reqs must be a positive integer, not '{!r}'".format(n_reqs))
        self.n_reqs = n_reqs
        self.policy = policy
        self.i = Signal(n_reqs)
        self.o = Signal(n_reqs)
        if policy == RobinPolicy.CE:
            self.en = Signal()

    def elaborate(self, platform):
        m = Module()

        grant_prev = Signal(self.n_reqs)
        req_unwrapped = Cat(self.i & _smear_lx(grant_prev), self.i)
        psel = m.submodules.psel = PrioritySelector(self.n_reqs * 2)
        m.d.comb += psel.i.eq(req_unwrapped)
        grant = psel.o[:self.n_reqs] | psel.o[self.n_reqs:]

        if self.policy == RobinPolicy.WITHDRAW:
            en = ~((self.i & grant_prev).bool())
        else:
            en = self.en

        with m.If(en):
            m.d.sync += grant_prev.eq(grant)
            m.d.comb += self.o.eq(grant)
        with m.Else():
            m.d.comb += self.o.eq(grant_prev)

        return m

class RoundRobinEncoder:
    """Identical to :class:`RoundRobinSelector`, except its output is encoded.

    Attributes
    ----------
    i : Signal(n_reqs), in
        Input request bitmap.
    o : Signal(max=max(2, n_reqs)), out
        Output grant index.
        There is no register on the path between ``i`` and ``o``;
        requests can be presented and granted in-cycle.
    n : Signal(1), out
        1 if no request is currently granted.
    en : Signal(1), in
        Request enable. Only present if ``policy`` is CE.
        A newly-arbitrated grant bitmap is presented on ``o`` on
        cycles where ``en`` is asserted.
        Otherwise, ``o`` does not change.
    """
    def __init__(self, n_reqs, policy=RobinPolicy.CE):
        self.n_reqs = n_reqs
        self.policy = policy
        self.i = Signal(n_reqs)
        self.o = Signal(max=max(2,n_reqs))
        self.n = Signal()
        if policy == RobinPolicy.CE:
            self.en = Signal()

    def elaborate(self, platform):
        m = Module()

        robin = m.submodules.robin = \
            RoundRobinSelector(self.n_reqs, self.policy)
        enc = m.submodules.enc = Encoder(self.n_reqs)

        if self.policy == RobinPolicy.CE:
            m.d.comb += robin.en.eq(self.en)

        m.d.comb += [
            robin.i.eq(self.i),
            enc.i.eq(robin.o),
            self.o.eq(enc.o),
            self.n.eq(enc.n)
        ]

        return m