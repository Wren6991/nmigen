from .tools import *
from ..hdl.ast import *
from ..hdl.cd import *
from ..hdl.dsl import *
from ..back.pysim import *
from ..lib.coding import *


class EncoderTestCase(FHDLTestCase):
    def test_basic(self):
        enc = Encoder(4)
        with Simulator(enc) as sim:
            def process():
                self.assertEqual((yield enc.n), 1)
                self.assertEqual((yield enc.o), 0)

                yield enc.i.eq(0b0001)
                yield Delay()
                self.assertEqual((yield enc.n), 0)
                self.assertEqual((yield enc.o), 0)

                yield enc.i.eq(0b0100)
                yield Delay()
                self.assertEqual((yield enc.n), 0)
                self.assertEqual((yield enc.o), 2)

                yield enc.i.eq(0b0110)
                yield Delay()
                self.assertEqual((yield enc.n), 1)
                self.assertEqual((yield enc.o), 0)

            sim.add_process(process)
            sim.run()


class PriorityEncoderTestCase(FHDLTestCase):
    def test_basic(self):
        enc = PriorityEncoder(4)
        with Simulator(enc) as sim:
            def process():
                self.assertEqual((yield enc.n), 1)
                self.assertEqual((yield enc.o), 0)

                yield enc.i.eq(0b0001)
                yield Delay()
                self.assertEqual((yield enc.n), 0)
                self.assertEqual((yield enc.o), 0)

                yield enc.i.eq(0b0100)
                yield Delay()
                self.assertEqual((yield enc.n), 0)
                self.assertEqual((yield enc.o), 2)

                yield enc.i.eq(0b0110)
                yield Delay()
                self.assertEqual((yield enc.n), 0)
                self.assertEqual((yield enc.o), 1)

            sim.add_process(process)
            sim.run()


class DecoderTestCase(FHDLTestCase):
    def test_basic(self):
        dec = Decoder(4)
        with Simulator(dec) as sim:
            def process():
                self.assertEqual((yield dec.o), 0b0001)

                yield dec.i.eq(1)
                yield Delay()
                self.assertEqual((yield dec.o), 0b0010)

                yield dec.i.eq(3)
                yield Delay()
                self.assertEqual((yield dec.o), 0b1000)

                yield dec.n.eq(1)
                yield Delay()
                self.assertEqual((yield dec.o), 0b0000)

            sim.add_process(process)
            sim.run()


class PrioritySelectorSpec:
    def __init__(self, width):
        self.width = width

    def elaborate(self, platform):
        m = Module()
        sel = m.submodules.sel = PrioritySelector(self.width)

        m.d.comb += Assert(sel.i.bool() == sel.o.bool())
        if_elif = m.If
        for n in range(self.width):
            with if_elif(sel.i[n]):
                m.d.comb += Assert(sel.o[n])
            if_elif = m.Elif
        return m


class PrioritySelectorTest(FHDLTestCase):
    def test_formal1(self):
        self.check_formal(1)

    def test_formal10(self):
        self.check_formal(10)

    def check_formal(self, width):
        spec = PrioritySelectorSpec(width)
        self.assertFormal(spec, mode="prove")


class ReversibleSpec:
    def __init__(self, encoder_cls, decoder_cls, args):
        self.encoder_cls = encoder_cls
        self.decoder_cls = decoder_cls
        self.coder_args  = args

    def elaborate(self, platform):
        m = Module()
        enc, dec = self.encoder_cls(*self.coder_args), self.decoder_cls(*self.coder_args)
        m.submodules += enc, dec
        m.d.comb += [
            dec.i.eq(enc.o),
            Assert(enc.i == dec.o)
        ]
        return m


class HammingDistanceSpec:
    def __init__(self, distance, encoder_cls, args):
        self.distance    = distance
        self.encoder_cls = encoder_cls
        self.coder_args  = args

    def elaborate(self, platform):
        m = Module()
        enc1, enc2 = self.encoder_cls(*self.coder_args), self.encoder_cls(*self.coder_args)
        m.submodules += enc1, enc2
        m.d.comb += [
            Assume(enc1.i + 1 == enc2.i),
            Assert(sum(enc1.o ^ enc2.o) == self.distance)
        ]
        return m


class GrayCoderTestCase(FHDLTestCase):
    def test_reversible(self):
        spec = ReversibleSpec(encoder_cls=GrayEncoder, decoder_cls=GrayDecoder, args=(16,))
        self.assertFormal(spec, mode="prove")

    def test_distance(self):
        spec = HammingDistanceSpec(distance=1, encoder_cls=GrayEncoder, args=(16,))
        self.assertFormal(spec, mode="prove")


class RoundRobinSelectorSimpleCase(FHDLTestCase):
    def test_paramcheck(self):
        with self.assertRaises(TypeError):
            r = RoundRobinSelector(0)
        with self.assertRaises(TypeError):
            r = RoundRobinSelector(1, 0)
        with self.assertRaises(TypeError):
            r = RoundRobinSelector(1, "WITHDRAW")
        r = RoundRobinSelector(1, RobinPolicy.WITHDRAW)

    def test_walk_withdraw_2(self):
        self.check_walk_withdraw(2)

    def test_walk_withdraw_10(self):
        self.check_walk_withdraw(10)

    def test_walk_enable_2(self):
        self.check_walk_enable(2)

    def test_walk_enable_10(self):
        self.check_walk_enable(10)

    def check_tick(self, rr, bits):
        yield Delay(1e-8)
        self.assertEqual((yield rr.o), bits)
        yield Tick()

    def check_walk_withdraw(self, width):
        m = Module()
        m.domains += ClockDomain("sync")
        rr = m.submodules.rr = RoundRobinSelector(width, RobinPolicy.WITHDRAW)
        with Simulator(m, vcd_file = open("test.vcd", "w")) as sim:
            sim.add_clock(1e-6)
            def process():
                yield rr.i.eq(0)
                yield Tick()
                yield Tick()
                for i in range(width):
                    j = (i + 1) % width
                    yield rr.i[i].eq(1)
                    yield from self.check_tick(rr, 1 << i)
                    yield rr.i[j].eq(1)
                    yield from self.check_tick(rr, 1 << i)
                    yield rr.i[i].eq(0)
                    yield from self.check_tick(rr, 1 << j)
                    yield rr.i[j].eq(0)
                    yield from self.check_tick(rr, 0)
            sim.add_process(process)
            sim.run()

    def check_walk_enable(self, width):
        m = Module()
        m.domains += ClockDomain("sync")
        rr = m.submodules.rr = RoundRobinSelector(width, RobinPolicy.CE)
        with Simulator(m, vcd_file = open("test.vcd", "w")) as sim:
            sim.add_clock(1e-6)
            def process():
                yield rr.i.eq(0)
                yield rr.en.eq(0)
                yield Tick()
                for i in range(width):
                    j = (i + 1) % width
                    yield rr.i[i].eq(1)
                    yield rr.en.eq(1)
                    yield from self.check_tick(rr, 1 << i)
                    yield from self.check_tick(rr, 1 << i)
                    yield rr.i[j].eq(1)
                    yield rr.en.eq(0)
                    yield from self.check_tick(rr, 1 << i)
                    yield rr.en.eq(1)
                    yield from self.check_tick(rr, 1 << j)
                    yield from self.check_tick(rr, 1 << i)
                    yield from self.check_tick(rr, 1 << j)
                    yield rr.i[i].eq(0)
                    yield from self.check_tick(rr, 1 << j)
                    yield from self.check_tick(rr, 1 << j)
                    yield rr.i[j].eq(0)
                    yield rr.en.eq(0)
                    yield from self.check_tick(rr, 1 << j)
                    yield rr.en.eq(1)
                    yield from self.check_tick(rr, 0)
            sim.add_process(process)
            sim.run()
