#!/usr/bin/env python3
"""XPan generator helpers that need no firmware: PNG codec, swap-table packing, branch encoding, a64 encoders."""
import random
import struct
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from z8prores.a64 import Block
from z8xpan import patcher

# Block encoders added for XPan: (call, Capstone text, word); the words are the hand encodings they replaced
ENCODERS = (
    (lambda b: b.cmp_x(4, 12), 'cmp x4, x12', 0xEB0C009F),
    (lambda b: b.cmp_xi(12, 8), 'cmp x12, #8', 0xF100219F),
    (lambda b: b.cmn_xi(10, 1), 'cmn x10, #1', 0xB100055F),
    (lambda b: b.sub_x(17, 13, 17), 'sub x17, x13, x17', 0xCB1101B1),
    (lambda b: b.add_w(8, 14, 14, lsl=3), 'add w8, w14, w14, lsl #3', 0x0B0E0DC8),
    (lambda b: b.add_w(16, 16, 9), 'add w16, w16, w9', 0x0B090210),
    (lambda b: b.lsr_wi(8, 8, 4), 'lsr w8, w8, #4', 0x53047D08),
    (lambda b: b.udiv_w(10, 10, 16), 'udiv w10, w10, w16', 0x1AD0094A),
    (lambda b: b.umull(12, 14, 13), 'umull x12, w14, w13', 0x9BAD7DCC),
    (lambda b: b.mul_w(11, 11, 17), 'mul w11, w11, w17', 0x1B117D6B),
    (lambda b: b.and_w_mask(13, 13, 14), 'and w13, w13, #0x3fff', 0x120035AD),
    (lambda b: b.and_w_mask(2, 0, 1), 'and w2, w0, #1', 0x12000002),
    (lambda b: b.and_w_mask(17, 10, 8), 'and w17, w10, #0xff', 0x12001D51),
    (lambda b: b.and_w3(5, 6), 'and w5, w6, #3', 0x120004C5),
    (lambda b: b.orr_w(17, 17, 16), 'orr w17, w17, w16', 0x2A100231),
    (lambda b: b.orr_w_bit(17, 17, 7), 'orr w17, w17, #0x80', 0x32190231),
    (lambda b: b.orr_w_bit(3, 4, 0), 'orr w3, w4, #1', 0x32000083),
    (lambda b: b.csel_w(19, 17, 31, 'eq'), 'csel w19, w17, wzr, eq', 0x1A9F0233),
    (lambda b: b.csel_w(1, 2, 3, 'lo'), 'csel w1, w2, w3, lo', 0x1A833041),
    (lambda b: b.ldrh_idx(10, 7, 13), 'ldrh w10, [x7, x13]', 0x786D68EA),
    (lambda b: b.strh_idx(10, 7, 13), 'strh w10, [x7, x13]', 0x782D68EA),
    (lambda b: b.ldr_x_idx(10, 7, 9), 'ldr x10, [x7, x9]', 0xF86968EA),
    (lambda b: b.str_x_idx(31, 7, 9), 'str xzr, [x7, x9]', 0xF82968FF),
    (lambda b: b.strb_idx(17, 7, 13), 'strb w17, [x7, x13]', 0x382D68F1),
    (lambda b: b.ldurb(16, 8, -2), 'ldurb w16, [x8, #-2]', 0x385FE110),
    (lambda b: b.sturb(16, 10, -2), 'sturb w16, [x10, #-2]', 0x381FE150),
    (lambda b: b.movn_w(19, 0), 'mov w19, #-1', 0x12800013),
    (lambda b: b.movn_x(7, 15), 'mov x7, #-0x10', 0x928001E7),
    (lambda b: b.dc_civac(10), 'dc civac, x10', 0xD50B7E2A),
    (lambda b: b.dsb_sy(), 'dsb sy', 0xD5033F9F),
)


class XPanUnits(unittest.TestCase):
    def test_png_round_trip_is_exact_and_deterministic(self):
        rng = random.Random(7)
        rows = [bytearray(rng.randrange(256) for _ in range(36)) for _ in range(32)]
        png = patcher.png_encode(rows)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(patcher.png_grey(png), rows)
        self.assertEqual(patcher.png_encode(rows), png)

    def test_png_grey_rejects_other_formats(self):
        with self.assertRaises(ValueError):
            patcher.png_grey(b'not a png')

    def test_swap_table_packing(self):
        rows = [(0xA02A94744, 0x12200000 | 8256, 0x0BE80000 | 8256), (0xA02A5CEC0, 25, 13)]
        packed = patcher.swap_table(rows)
        self.assertEqual(len(packed), 16 * len(rows))
        self.assertEqual([struct.unpack_from('<QII', packed, 16 * i) for i in range(len(rows))], rows)

    def test_branch_encoding_and_range(self):
        self.assertEqual(patcher.branch(0x40000000, 0x40000004), struct.pack('<I', 0x14000001))
        self.assertEqual(patcher.branch(0x40000000, 0x40000004, link=True), struct.pack('<I', 0x94000001))
        self.assertEqual(patcher.branch(0x40000010, 0x40000000), struct.pack('<I', 0x17FFFFFC))
        with self.assertRaises(ValueError):
            patcher.branch(0x40000000, 0x40000000 + (1 << 27))
        with self.assertRaises(ValueError):
            patcher.branch(0x40000000, 0x40000002)

    def test_mov_x0_form(self):
        self.assertEqual(patcher.mov_x0(0xA02823428), struct.pack('<4I', 0xD2868500, 0xF2A05040, 0xF2C00140, 0xF2E00000))

    def test_a64_encoders_disassemble_as_asked(self):
        blk = Block(0x40000000)
        for emit, _, _ in ENCODERS:
            emit(blk)
        blk.mov_w32(0, 0x07000603)
        blk.cbnz_x(17, 0x40000000)
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        listing = blk.listing(md)
        expected = [t for _, t, _ in ENCODERS] + ['mov w0, #0x603', 'movk w0, #0x700, lsl #16', 'cbnz x17, #0x40000000']
        self.assertEqual([t for _, _, t in listing], expected)
        self.assertEqual(blk.words()[:len(ENCODERS)], [w for _, _, w in ENCODERS])
        with self.assertRaises(ValueError):
            blk.ldurb(0, 1, 256)
        with self.assertRaises(ValueError):
            blk.and_w_mask(0, 1, 33)

    def test_raw_words_match_their_text(self):
        """Every replayed stock word and barrier the patcher emits through raw() reads back as the text given."""
        listing = patcher.code(1, 0x1000).listing(Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN))
        seen = {int.from_bytes(bytes.fromhex(w), 'little'): t for _, w, t in listing}
        self.assertTrue(patcher.RAWS)
        for word, text in patcher.RAWS.items():
            self.assertEqual(seen[word], text, f'{word:#x}')


if __name__ == '__main__':
    unittest.main()
