"""XPan image area (prototype, milestones 1-2): a sixth "Choose image area" row, XPan (36x13), and 65:24 bars.

XPan runs as the stock 16:9 image area (DS 07000603h = 4) plus a flag, so sensor readout, AF and every
consumer of the image-area value keep their stock 16:9 behaviour. Milestone 1 adds the menu row:

  page      photo "Choose image area" (GUI state 2Ah): five stock rows FX, DX, 5:4 (hidden), 1:1, 16:9
            plus XPan, a copy of the 16:9 row with its own label and item 35BBh
  flag      byte A03D482C2, the padding between DS 07000602h (4 bytes at A03D482BE) and 07000603h
            (A03D482C4) in the photo-bank group; 1 = XPan, meaningful only while the image area is 16:9
  rows OK   every stock row clears the flag, then runs its stock setter; XPan sets the flag and 16:9
  entry     cursor and mark follow the stock value mapping, XPan when 16:9 and the flag is set
  enable    XPan is available exactly when 16:9 is (stock condition 1Bh)
  banks     the per-item slot copy (410161F0), 32-bit case at 410162C4, also copies the flag byte with
            07000603h, so each photo shooting menu bank keeps its own choice

Milestone 2 paints the bars on the rear monitor. Stock 16:9 live view is a full-screen 1024x682 video plane
whose picture occupies rows 53..628; the GUI overlay above it is YYUV plus a separate alpha plane, three
rotating buffers, presented by 402CF85C (colour address, then bank request 402AB1CC at 402CF9D0).

  state     402AA36C / 402AA498 (direct input start/stop of unit 15484000h) set or clear a runtime byte
            at 408CFF00 (written through the text section's RAM alias A0F800000)
  present   before the bank request, with XPan active and live view running, every overlay pixel the GUI
            left transparent in rows 0..151 and 530..681 becomes opaque black; icons stay. The 65:24 window
            is rows 152..529, the centre 378 rows of the 576-row 16:9 picture. Cache lines are cleaned and
            invalidated around the CPU writes.
The EVF, HDMI and playback are untouched.
Used by features.compose; offline only.
"""
import hashlib
import re
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from z8prores.a64 import Block
from z8prores.menu import Image
from z8prores.package import require

BASE, DATA_OFF, DATA_VA = 0x40160000, 0x4B13000, 0xA02808000
CODE_AT, GUI_AT, GUI_END = 0x766000, 0x767000, 0x770000      # free text gap below the shared strings
STATE = 0x4C00C80                                            # GUI state 2Ah record (data image)
STOCK_CALLBACKS, STOCK_PAGE = 0xA028E6E40, 0xA028CC9D0
CALLBACKS = (0x405DBE0C, 0x405DBEA4, 0x403B2210, 0x405DBCD0, 0)
ROWS, EVENTS = 0x43DD2A8C, 0x43DD2B68
LABELS = (0x533, 0x30C, 0x43A, 0x534, 0x582)                 # FX, DX, 5:4, 1:1, 16:9
ITEMS = (0x2BEB, 0x29B8, 0x2BE9, 0x2BEA, 0x2C5F)
STUBS = (0x405E69C0, 0x405E69F0, 0x405E69D0, 0x405E69E0, 0x405E6A10)   # SetDS(07000603h, 0/1/2/8/4)
STUB_VALUES = (0, 1, 2, 8, 4)
OK_EVENTS, END_EVENT = (0x286, 0x28B, 0x2AE), 955
ITEM_XPAN = 0x35BB                                           # unused item id (35B5..35BA: ProRes, N-RAW)
ITEM_ID, VALUE_169 = 0x07000603, 4
FLAG = 0xA03D482C2
COPY_SITE, COPY_NEXT, COPY_WORD = 0x410162C4, 0x410162C8, 0xA94023EA   # ldp x10,x8,[sp]
F_GET, F_SET = 0x403EC398, 0x403EC39C
F_ROW, F_ROW_STATE, F_ENTRY_GUARD = 0x403AF870, 0x403B1ED0, 0x403AE0F8
F_SET_CURSOR, F_SET_MARK = 0x403B0048, 0x403B00B8
F_CONDITION, F_ENABLE = 0x403A036C, 0x403B1A5C
COND_BLOCKED, COND_169 = 0x19, 0x1B
PINNED = {0x405DBE0C: (0x98, '6e08bf0521ed62638837f6f84730b9880fe8af8c3bb851ea1b9934473a9a2803'),
          0x405DBCD0: (0x7C, '9b771967eee754f7e6a8820bc7f6167a3132a55230bb67183572fcb79066b4ae'),
          0x405E69C0: (0x60, '84dbd5338c987625ad3d97a3c6201140ad886e1f95bf30dc1887217bea563d02')}
STOCK_LABEL = 0x582                                          # "16:9 (36×20)" and its 35 translations
TABLE_DIR, BLOB_DIR = 0x311C5A0, 0x387AF58
# milestone 2: opaque bars painted into a display's overlay just before each frame is presented
LCD_BASE, EVF_BASE = 0x15484000, 0x15480000                  # display units (identity MMIO): rear monitor, EVF
START_SITE, START_NEXT = 0x402AA36C, 0x402AA370              # str w9,[x8,#0x100]: direct input start
STOP_SITE, STOP_NEXT = 0x402AA498, 0x402AA49C                # str w9,[x8,#0x100]: direct input stop
START_WORD = 0xB9010109
PRESENT_SITE, F_PRESENT = 0x402CF9D0, 0x402AB1CC             # bl: bank request after the overlay address
OSIZE_SITE, OSIZE_NEXT, OSIZE_WORD = 0x402AD198, 0x402AD19C, 0xB92401BB   # str w27,[x13,#0x2400]: overlay size
STATE_AT = 0x76FF00                                          # zero-initialized runtime state, see bars()
UNIT_LCD, UNIT_EVF = 0x10, 0x20                              # per unit: +0 running, +8 overlay w, +10 overlay h
OVERLAY_WIDTHS = (1024, 1280)                                # rear monitor 1024x682, EVF 1280x960
TEXT_SECTION, TEXT_ALIAS = 0x40200000, 0xA0F800000           # writable RAM alias of the copied text section
RAM_LO, RAM_HI = 0xA00000000, 0xB00000000                    # 402AB038 converts to the bus address itself
BLACK_Y, BLACK_PAIR = 0x00, 0x8080000080800000               # Y0 Y1 U V; the overlay conversion is full range
# milestone 3: 16:9 entries of the still-size table (RAM data, read through 41061928) and their 65:24 values;
# raw readout (8280x4656) is left at 16:9. Heights are multiples of 8 (thumbnails: of 2).
SIZES = ((0xA02A94744, (8256, 4640), (8256, 3048)), (0xA02A94748, (6192, 3480), (6192, 2288)),
         (0xA02A9474C, (4128, 2320), (4128, 1528)), (0xA02A94970, (8256, 4640), (8256, 3048)),
         (0xA02A94974, (8256, 4640), (8256, 3048)), (0xA02A94978, (8256, 4640), (8256, 3048)),
         (0xA02A947E0, (640, 360), (640, 236)), (0xA02A947F4, (320, 180), (320, 118)),
         (0xA02A94808, (1920, 1080), (1920, 712)), (0xA02A9481C, (160, 90), (160, 60)))
SIZE_TABLE_AT = 0x76FE00                                     # read-only copy of SIZES for the sync routine


def ram(address):
    return address - TEXT_SECTION + TEXT_ALIAS


def branch(at, to, link=False):
    d = to - at
    require(d % 4 == 0 and -(1 << 27) <= d < (1 << 27), 'Branch target out of range')
    return struct.pack('<I', (0x94000000 if link else 0x14000000) | ((d // 4) & 0x3FFFFFF))


# AArch64 forms the shared assembler lacks; each is checked against Capstone in build_main.
def ldr_xr(rt, rn, rm): return 0xF8606800 | rm << 16 | rn << 5 | rt
def str_xr(rt, rn, rm): return 0xF8206800 | rm << 16 | rn << 5 | rt
def strb_r(rt, rn, rm): return 0x38206800 | rm << 16 | rn << 5 | rt
def cmp_xi(rn, imm): return 0xF100001F | imm << 10 | rn << 5
def cmp_x(rn, rm): return 0xEB00001F | rm << 16 | rn << 5
def cmn_x1(rn): return 0xB100041F | rn << 5
def sub_x(rd, rn, rm): return 0xCB000000 | rm << 16 | rn << 5 | rd
def and_x1(rd, rn): return 0x92400000 | rn << 5 | rd
def movn_x0(rd): return 0x92800000 | rd
def dc_civac(rt): return 0xD50B7E20 | rt
def add_w_lsl(rd, rn, rm, sh): return 0x0B000000 | rm << 16 | sh << 10 | rn << 5 | rd
def lsr_wi(rd, rn, sh): return 0x53000000 | sh << 16 | 31 << 10 | rn << 5 | rd
def udiv_w(rd, rn, rm): return 0x1AC00800 | rm << 16 | rn << 5 | rd
def umull(rd, rn, rm): return 0x9BA07C00 | rm << 16 | rn << 5 | rd
DSB_SY = 0xD5033F9F


def cbnz_x(blk, rt, target):
    blk._rel(lambda pc: 0xB5000000 | ((blk._target(target) - pc) >> 2 & 0x7FFFF) << 5 | rt)


def va(offset):
    return BASE + offset


def off(address):
    return address - DATA_VA + DATA_OFF if address >= DATA_VA else address - BASE


def label(img):
    """Each language's stock 16:9 row label with the name and height swapped: XPan (36x13)."""
    out = []
    for k in range(36):
        table, blob = img.u64(TABLE_DIR + 8 * k) - BASE, img.u64(BLOB_DIR + 8 * k) - BASE
        a, b = struct.unpack_from('<2i', img.old, table + 4 * STOCK_LABEL)
        text = img.old[blob + a:blob + b].decode('utf-16le')
        new, height = re.subn(r'20\)$', '13)', text.replace('16:9', 'XPan'))
        require(text.count('16:9') == 1 and height == 1, 'Unexpected stock 16:9 label')
        out.append([new])
    return out


def code():
    blk = Block(va(CODE_AT))
    # OK actions: stock rows clear the flag first; XPan stores 16:9 with the flag set
    for name, stub in zip(('ok_fx', 'ok_dx', 'ok_54', 'ok_11', 'ok_169'), STUBS):
        blk.label(name)
        blk.mov_x64(16, FLAG)
        blk.strb_w(31, 16)
        blk.b(stub)
    blk.label('ok_xpan')                        # 16:9 first, then the flag: sync clears a flag without 16:9
    blk.stp_pre(19, 30, -16)
    blk.mov_w(0, ITEM_ID & 0xFFFF)
    blk.movk_w16(0, ITEM_ID >> 16)
    blk.mov_w(1, VALUE_169)
    blk.bl(F_SET)
    blk.mov_x64(16, FLAG)
    blk.mov_w(17, 1)
    blk.strb_w(17, 16)
    blk.ldp_post(19, 30, 16)
    blk.ret()
    # entry: stock 405DBE0C with the XPan case added to the value-to-row mapping
    blk.label('entry')
    blk.stp_pre(19, 30, -32)
    blk.stp(20, 21, 16)
    blk.mov_x(19, 0)
    blk.mov_w(0, ITEM_ID & 0xFFFF)
    blk.movk_w16(0, ITEM_ID >> 16)
    blk.bl(F_GET)
    blk.cmp_wi(0, 8)
    blk.b_cond('eq', 'map_11')
    blk.cmp_wi(0, 4)
    blk.b_cond('eq', 'map_169')
    blk.cmp_wi(0, 1)
    blk.b_cond('ne', 'map_fx')
    blk.mov_w(20, ITEMS[1])
    blk.b('mapped')
    blk.label('map_11')
    blk.mov_w(20, ITEMS[3])
    blk.b('mapped')
    blk.label('map_169')
    blk.mov_w(20, ITEMS[4])
    blk.mov_x64(16, FLAG)
    blk.ldrb_w(16, 16)
    blk.cbz_w(16, 'mapped')
    blk.mov_w(20, ITEM_XPAN)
    blk.b('mapped')
    blk.label('map_fx')
    blk.mov_w(20, ITEMS[0])
    blk.label('mapped')
    blk.mov_w(1, ITEMS[2])
    blk.mov_x(0, 19)
    blk.bl(F_ROW)
    blk.mov_wr(1, 0)
    blk.mov_w(2, 1)
    blk.mov_x(0, 19)
    blk.bl(F_ROW_STATE)
    blk.bl(F_ENTRY_GUARD)
    blk.cmp_wi(0, 1)
    blk.b_cond('eq', 'mark')
    blk.mov_x(0, 19)
    blk.mov_wr(1, 20)
    blk.bl(F_SET_CURSOR)
    blk.label('mark')
    blk.mov_x(0, 19)
    blk.mov_wr(1, 20)
    blk.mov_w(2, 1)
    blk.ldp(20, 21, 16)
    blk.ldp_post(19, 30, 32)
    blk.b(F_SET_MARK)
    # enable: stock 405DBCD0, then XPan follows the 16:9 condition
    blk.label('enable')
    blk.stp_pre(19, 30, -32)
    blk.stp(20, 21, 16)
    blk.mov_x(19, 0)
    blk.bl(CALLBACKS[3])
    blk.mov_wr(20, 0)
    blk.mov_w(0, COND_BLOCKED)
    blk.mov_x(1, 19)
    blk.bl(F_CONDITION)
    blk.tbz_w(0, 0, 'enable_row')
    blk.b('enable_done')
    blk.label('enable_row')
    blk.mov_w(0, COND_169)
    blk.mov_x(1, 19)
    blk.bl(F_CONDITION)
    blk.raw(0x12000002)                     # and w2,w0,#1
    blk.mov_w(1, ITEM_XPAN)
    blk.mov_x(0, 19)
    blk.bl(F_ENABLE)
    blk.label('enable_done')
    blk.mov_wr(0, 20)
    blk.ldp(20, 21, 16)
    blk.ldp_post(19, 30, 32)
    blk.ret()
    # slot copy, 32-bit case: carry the flag byte with 07000603h (x8 source, x10 destination)
    blk.label('copy')
    blk.raw(COPY_WORD)
    blk.lsl_wi(9, 20, 16)
    blk.mov_w(11, 0)
    blk.movk_w16(11, ITEM_ID & 0xFFFF)
    blk.cmp_w(9, 11)
    blk.b_cond('ne', 'copy_done')
    blk.raw(0x385FE109)                     # ldurb w9,[x8,#-2]
    blk.raw(0x381FE149)                     # sturb w9,[x10,#-2]
    blk.label('copy_done')
    blk.b(COPY_NEXT)
    bars(blk)
    return blk


def bars(blk):
    """Milestones 2 and 2b: 65:24 bars in live view on the rear monitor and the EVF.

    Runtime state at STATE_AT (+1: size-table sync, see sizes()); per display unit at UNIT_LCD / UNIT_EVF:
    +0 live view running, +8/+10 overlay width and height, each recorded where the firmware programs them.
    The 16:9 picture is centred on the panel (rear monitor: rows 53..628 of 682).
    """
    raws = {}

    def raw(word, text):
        raws[word] = text
        blk.raw(word)

    def unit_state(base_reg, scratch, lcd_label, evf_label, none_label):
        """Branch to lcd_label or evf_label by display unit; flags are left untouched."""
        blk.mov_x64(scratch, LCD_BASE)
        raw(sub_x(scratch, base_reg, scratch), f'sub x{scratch}, x{base_reg}, x{scratch}')
        blk.cbz_x(scratch, lcd_label)
        blk.mov_x64(scratch, EVF_BASE)
        raw(sub_x(scratch, base_reg, scratch), f'sub x{scratch}, x{base_reg}, x{scratch}')
        blk.cbz_x(scratch, evf_label)
        blk.b(none_label)

    # direct input (live view) started / stopped, per unit
    for name, value, nxt in (('lv_start', 1, START_NEXT), ('lv_stop', 0, STOP_NEXT)):
        blk.label(name)
        raw(START_WORD, 'str w9, [x8, #0x100]')
        unit_state(8, 16, name + '_lcd', name + '_evf', name + '_done')
        blk.label(name + '_lcd')
        blk.mov_x64(16, ram(va(STATE_AT + UNIT_LCD)))
        blk.b(name + '_set')
        blk.label(name + '_evf')
        blk.mov_x64(16, ram(va(STATE_AT + UNIT_EVF)))
        blk.label(name + '_set')
        blk.mov_w(17, value)
        blk.strb_w(17, 16)
        blk.label(name + '_done')
        blk.b(nxt)
    # overlay size store (layer 0): only x17 is free here and the flags of a preceding tst are still live
    blk.label('osize')
    raw(OSIZE_WORD, 'str w27, [x13, #0x2400]')
    blk.mov_x64(17, LCD_BASE)
    raw(sub_x(17, 13, 17), 'sub x17, x13, x17')
    cbnz_x(blk, 17, 'osize_evf')
    blk.mov_x64(17, ram(va(STATE_AT + UNIT_LCD + 8)))
    blk.str_w(27, 17)
    blk.b('osize_done')
    blk.label('osize_evf')
    blk.mov_x64(17, EVF_BASE)
    raw(sub_x(17, 13, 17), 'sub x17, x13, x17')
    cbnz_x(blk, 17, 'osize_done')
    blk.mov_x64(17, ram(va(STATE_AT + UNIT_EVF + 8)))
    blk.str_w(27, 17)
    blk.label('osize_done')
    blk.b(OSIZE_NEXT)
    # bank request of the overlay: keep the size table in step with XPan (milestone 3), then paint when XPan
    # is active on a running live view
    blk.label('present')
    blk.mov_x(15, 30)
    blk.bl('sync')
    blk.mov_x(30, 15)
    blk.cmp_wi(1, 2)
    blk.b_cond('ne', 'flip')
    blk.mov_x64(16, FLAG)
    blk.ldrb_w(17, 16)
    blk.cbz_w(17, 'flip')
    blk.mov_x64(16, FLAG + 2)
    blk.ldr_w(17, 16)
    blk.cmp_wi(17, VALUE_169)
    blk.b_cond('ne', 'flip')
    blk.ldr_x(9, 0, 8)
    unit_state(9, 16, 'present_lcd', 'present_evf', 'flip')
    blk.label('present_lcd')
    blk.mov_x64(11, ram(va(STATE_AT + UNIT_LCD)))
    blk.b('present_unit')
    blk.label('present_evf')
    blk.mov_x64(11, ram(va(STATE_AT + UNIT_EVF)))
    blk.label('present_unit')
    blk.ldrb_w(17, 11)
    blk.cbz_w(17, 'flip')                       # live view not running on this unit
    blk.ldrh_w(14, 11, 8)                       # overlay (panel) width
    blk.ldrh_w(13, 11, 10)                      # overlay height
    blk.cmp_wi(14, OVERLAY_WIDTHS[0])
    blk.b_cond('eq', 'width_ok')
    blk.cmp_wi(14, OVERLAY_WIDTHS[1])
    blk.b_cond('ne', 'flip')
    blk.label('width_ok')
    raw(add_w_lsl(8, 14, 14, 3), 'add w8, w14, w14, lsl #3')
    raw(lsr_wi(8, 8, 4), 'lsr w8, w8, #4')      # 16:9 picture height = width * 9 / 16, centred on the panel
    blk.cmp_w(8, 13)
    blk.b_cond('hi', 'flip')
    blk.sub_w(9, 13, 8)
    raw(lsr_wi(9, 9, 1), 'lsr w9, w9, #1')      # 16:9 picture top
    blk.lsl_wi(10, 14, 3)
    raw(add_w_lsl(10, 10, 10, 1), 'add w10, w10, w10, lsl #1')
    blk.mov_w(16, 65)
    raw(udiv_w(10, 10, 16), 'udiv w10, w10, w16')
    raw(lsr_wi(10, 10, 1), 'lsr w10, w10, #1')
    blk.lsl_wi(10, 10, 1)                       # 65:24 height = width * 24 / 65, even
    blk.sub_w(16, 8, 10)
    raw(lsr_wi(16, 16, 1), 'lsr w16, w16, #1')
    blk.add_w(16, 16, 9)                        # first 65:24 row
    blk.add_w(17, 16, 10)                       # first row below the 65:24 window
    blk.ldr_x(4, 31, 0x28)                      # caller's overlay record: colour CPU address (bus = +400000000h)
    blk.mov_x64(12, RAM_LO)
    raw(cmp_x(4, 12), 'cmp x4, x12')
    blk.b_cond('lo', 'flip')
    blk.mov_x64(12, RAM_HI - 0x400000)
    raw(cmp_x(4, 12), 'cmp x4, x12')
    blk.b_cond('hs', 'flip')
    raw(umull(12, 14, 13), 'umull x12, w14, w13')
    blk.add_x(3, 4, 12, lsl=1)                  # alpha plane follows the YYUV plane
    blk.mov_x(15, 30)
    blk.stp_pre(17, 13, -16)
    blk.mov_w(5, 0)
    blk.mov_wr(6, 16)
    blk.bl('paint')
    blk.ldp_post(5, 6, 16)
    blk.bl('paint')
    raw(DSB_SY, 'dsb sy')
    blk.mov_x(30, 15)
    blk.label('flip')
    blk.b(F_PRESENT)
    # paint rows [w5, w6) of width x14: x3 alpha plane, x4 YYUV plane; transparent -> opaque black, icons kept
    blk.label('paint')
    blk.label('row')
    blk.cmp_w(5, 6)
    blk.b_cond('hs', 'paint_done')
    blk.madd_x(7, 5, 14, 3)
    blk.madd_x(8, 5, 14, 31)
    blk.add_x(8, 4, 8, lsl=1)
    for phase in ('inv', 'cln'):
        if phase == 'cln':
            blk.label('px_done')
        blk.mov_w(9, 0)
        blk.label(phase)
        blk.add_x(10, 7, 9)
        raw(dc_civac(10), 'dc civac, x10')
        blk.add_x(11, 8, 9, lsl=1)
        raw(dc_civac(11), 'dc civac, x11')
        blk.add_xi(11, 11, 64)
        raw(dc_civac(11), 'dc civac, x11')
        blk.add_xi(9, 9, 64)
        raw(cmp_x(9, 14), 'cmp x9, x14')
        blk.b_cond('lo', phase)
        raw(DSB_SY, 'dsb sy')
        if phase == 'inv':
            blk.mov_w(9, 0)
            blk.label('px')
            raw(ldr_xr(10, 7, 9), 'ldr x10, [x7, x9]')
            blk.cbz_x(10, 'fill')
            raw(cmn_x1(10), 'cmn x10, #1')
            blk.b_cond('eq', 'next')
            blk.mov_w(12, 0)
            blk.label('slow')
            blk.add_x(13, 9, 12)
            blk.ldrb_idx(10, 7, 13)
            blk.cmp_wi(10, 255)
            blk.b_cond('eq', 'slow_next')
            blk.cbnz_w(10, 'opaque')
            blk.add_x(16, 8, 13, lsl=1)         # Y of pixel p: 2p - (p & 1)
            raw(and_x1(17, 13), 'and x17, x13, #1')
            raw(sub_x(16, 16, 17), 'sub x16, x16, x17')
            blk.mov_w(17, BLACK_Y)
            blk.strb_w(17, 16)
            blk.label('opaque')
            blk.mov_w(16, 255)
            raw(strb_r(16, 7, 13), 'strb w16, [x7, x13]')
            blk.label('slow_next')
            blk.add_xi(12, 12, 1)
            raw(cmp_xi(12, 8), 'cmp x12, #8')
            blk.b_cond('lo', 'slow')
            blk.b('next')
            blk.label('fill')
            raw(movn_x0(11), 'mov x11, #-1')
            raw(str_xr(11, 7, 9), 'str x11, [x7, x9]')
            blk.add_x(12, 8, 9, lsl=1)
            blk.mov_x64(13, BLACK_PAIR)
            blk.str_x(13, 12, 0)
            blk.str_x(13, 12, 8)
            blk.label('next')
            blk.add_xi(9, 9, 8)
            raw(cmp_x(9, 14), 'cmp x9, x14')
            blk.b_cond('lo', 'px')
            blk.b('px_done')
    blk.add_xi(5, 5, 1)
    blk.b('row')
    blk.label('paint_done')
    blk.ret()
    sizes(blk)
    blk.bars_raws = raws


def sizes(blk):
    """Milestone 3: while XPan is active the 16:9 entries of the still-size table hold 65:24 sizes."""
    blk.label('sync')
    blk.mov_x64(16, FLAG)
    blk.ldrb_w(17, 16)
    blk.mov_x64(16, FLAG + 2)
    blk.ldr_w(9, 16)
    blk.mov_w(10, 0)
    blk.cbz_w(17, 'sync_have')
    blk.cmp_wi(9, VALUE_169)
    blk.b_cond('eq', 'sync_active')
    blk.mov_x64(16, FLAG)                       # image area left 16:9 by another path (dial, i menu, reset):
    blk.strb_w(31, 16)                          # XPan no longer applies
    blk.b('sync_have')
    blk.label('sync_active')
    blk.mov_w(10, 1)
    blk.label('sync_have')
    blk.mov_x64(16, ram(va(STATE_AT + 1)))
    blk.ldrb_w(11, 16)
    blk.cmp_w(11, 10)
    blk.b_cond('eq', 'sync_done')
    blk.strb_w(10, 16)
    blk.mov_x64(12, va(SIZE_TABLE_AT))
    blk.mov_w(13, len(SIZES))
    blk.label('sync_row')
    blk.ldr_x(14, 12, 0)
    blk.ldr_w(9, 12, 8)
    blk.cbz_w(10, 'sync_store')
    blk.ldr_w(9, 12, 12)
    blk.label('sync_store')
    blk.str_w(9, 14, 0)
    blk.add_xi(12, 12, 16)
    blk.subs_wi(13, 13, 1)
    blk.b_cond('ne', 'sync_row')
    blk.label('sync_done')
    blk.ret()


def size_table():
    """{RAM address, stock w|h<<16, XPan w|h<<16} per entry, read by the sync routine."""
    return b''.join(struct.pack('<QII', address, stock[0] | stock[1] << 16, xpan[0] | xpan[1] << 16)
                    for address, stock, xpan in SIZES)


def check_stock(img):
    for at, (size, digest) in PINNED.items():
        require(hashlib.sha256(img.old[off(at):off(at) + size]).hexdigest() == digest, f'Unexpected stock code at {at:#x}')
    state = struct.unpack_from('<HHI11Q', img.old, STATE)
    require(state[:2] == (2, 0x2A) and state[7] == STOCK_CALLBACKS and state[12] == STOCK_PAGE, 'Unexpected image-area GUI state')
    require(struct.unpack_from('<5Q', img.old, off(STOCK_CALLBACKS)) == CALLBACKS, 'Unexpected image-area callbacks')
    page = img.old[off(STOCK_PAGE):off(STOCK_PAGE) + 80]
    require(struct.unpack_from('<2Q', page, 16) == (ROWS, EVENTS) and struct.unpack_from('<H', page, 32)[0] == 5,
            'Unexpected image-area page record')
    rows = [struct.unpack_from('<11I', img.old, off(ROWS) + 44 * i) for i in range(5)]
    require(tuple(r[0] for r in rows) == LABELS and tuple(r[6] for r in rows) == ITEMS, 'Unexpected image-area rows')
    require(img.u32(off(COPY_SITE)) == COPY_WORD and img.u32(off(COPY_NEXT)) == 0xB9400109, 'Unexpected slot copy code')
    # direct input start/stop: ldr x8,[x0,#8] ; mov w9,#1|#2 ; str w9,[x8,#0x100]
    require(struct.unpack_from('<3I', img.old, off(START_SITE) - 8) == (0xF9400408, 0x320003E9, START_WORD) and
            struct.unpack_from('<3I', img.old, off(STOP_SITE) - 8) == (0xF9400408, 0x321F03E9, START_WORD),
            'Unexpected display start/stop code')
    # overlay present: str x8,[sp,#0x28] ; bl 402ab038 ; cbnz ; ldr x0,[x29] ; mov w1,w19 ; mov w2,wzr ; bl 402ab1cc
    require(img.u32(off(PRESENT_SITE) - 0x18) == 0xF90017E8 and img.old[off(PRESENT_SITE):off(PRESENT_SITE) + 4] ==
            branch(PRESENT_SITE, F_PRESENT, link=True), 'Unexpected overlay present code')
    # 402ad194: tst w20,#0xff ; str w27,[x13,#0x2400] ; b.eq (x16 is live across, x17 is not)
    require(struct.unpack_from('<3I', img.old, off(OSIZE_SITE) - 4) == (0x72001E9F, OSIZE_WORD, 0x54000180),
            'Unexpected overlay size code')
    # the flag byte is padding: 07000602h ends at A03D482C2 and 07000603h starts at A03D482C4
    entry = lambda lo: struct.unpack_from('<IIQ', img.old, off(0x4400A5C0 + 0x18 * (233 + lo)))
    require(entry(2) == (10, 4, FLAG - 4) and entry(3) == (9, 4, FLAG + 2), 'Unexpected photo-bank DS layout')
    return page, rows


def tables(img, blk):
    """Per-row transition tables: the stock table of each row with its OK action pointed at the new wrapper."""
    out = []
    pointers = struct.unpack_from('<5Q', img.old, off(EVENTS))
    for ptr, stub, name in zip(pointers, STUBS, ('ok_fx', 'ok_dx', 'ok_54', 'ok_11', 'ok_169')):
        recs = []
        for i in range(32):
            rec = struct.unpack_from('<4Q', img.old, off(ptr) + 32 * i)
            recs.append(rec)
            if rec[0] == END_EVENT:
                break
        require(recs[-1][0] == END_EVENT and {r[0] for r in recs if r[1] == stub} == set(OK_EVENTS), 'Unexpected row transitions')
        out.append([(e, blk.labels[name] if a == stub else a, g, t) for e, a, g, t in recs])
    strip = lambda recs, stub: [(e, 0 if e in OK_EVENTS else a, g, t) for e, a, g, t in recs]
    require(all(strip(t, blk.labels[n]) == strip(out[4], blk.labels['ok_169'])
                for t, n in zip(out, ('ok_fx', 'ok_dx', 'ok_54', 'ok_11', 'ok_169'))), 'Row transitions differ beyond OK')
    out.append([(e, blk.labels['ok_xpan'] if e in OK_EVENTS else a, g, t) for e, a, g, t in out[4]])
    return [b''.join(struct.pack('<4Q', *r) for r in t) for t in out]


def build_main(data, strings):
    """Return Image edits and review metadata; input is immutable MAIN without CRC."""
    img = Image(data)
    page, rows = check_stock(img)
    label_id, = strings.add('xpan', label(img))
    blk = code()
    raw = blk.bytes()
    require(CODE_AT + len(raw) <= GUI_AT, 'XPan code too large')
    row = struct.pack('<11I', label_id, *rows[4][1:6], ITEM_XPAN, *rows[4][7:])
    row_bytes = b''.join(struct.pack('<11I', *r) for r in rows) + row
    at = GUI_AT
    rows_at, at = at, at + len(row_bytes)
    at = (at + 7) & ~7
    trans = tables(img, blk)
    trans_at = []
    for t in trans:
        trans_at.append(at)
        at += len(t)
    ptrs_at, at = at, at + 8 * len(trans)
    callbacks_at, at = at, at + 40
    page_at, at = at, at + 80
    require(at <= GUI_END, 'XPan GUI area too small')
    new_page = bytearray(page)
    struct.pack_into('<2Q', new_page, 16, va(rows_at), va(ptrs_at))
    struct.pack_into('<H', new_page, 32, 6)
    img.put(CODE_AT, raw, 'routine: XPan menu row, entry/enable callbacks, flag bank copy', expect_zero=True)
    img.put(rows_at, row_bytes, 'Choose image area rows: stock five plus XPan', expect_zero=True)
    img.put(trans_at[0], b''.join(trans), 'row transition tables (stock, OK -> flag wrappers; XPan from 16:9)', expect_zero=True)
    img.put(ptrs_at, b''.join(struct.pack('<Q', va(a)) for a in trans_at), 'row transition table pointers', expect_zero=True)
    img.put(callbacks_at, struct.pack('<5Q', blk.labels['entry'], CALLBACKS[1], CALLBACKS[2], blk.labels['enable'], 0),
            'Choose image area callbacks (new entry and enable, stock others)', expect_zero=True)
    img.put(page_at, bytes(new_page), 'Choose image area page record (stock layout, six rows)', expect_zero=True)
    img.put(STATE + 40, struct.pack('<Q', va(callbacks_at)), 'GUI state 2Ah: callbacks -> six-row page',
            expect=struct.pack('<Q', STOCK_CALLBACKS))
    img.put(STATE + 80, struct.pack('<Q', va(page_at)), 'GUI state 2Ah: page record -> six-row page',
            expect=struct.pack('<Q', STOCK_PAGE))
    img.put(off(COPY_SITE), branch(COPY_SITE, blk.labels['copy']),
            'slot copy, 32-bit case: hook for the XPan flag byte', expect=struct.pack('<I', COPY_WORD))
    require(at <= SIZE_TABLE_AT and SIZE_TABLE_AT + len(size_table()) <= STATE_AT, 'XPan GUI area overlaps its tables')
    for address, stock, _ in SIZES:
        require(img.u32(off(address)) == stock[0] | stock[1] << 16, f'Unexpected stock size at {address:#x}')
    img.put(SIZE_TABLE_AT, size_table(), 'still-size table entries: 16:9 and 65:24 values', expect_zero=True)
    img.put(STATE_AT, bytes(UNIT_EVF + 16), 'XPan runtime state: size sync, per-unit live view and geometry (zero at load)',
            expect_zero=True)
    img.put(off(START_SITE), branch(START_SITE, blk.labels['lv_start']),
            'display direct input start: record live view running', expect=struct.pack('<I', START_WORD))
    img.put(off(STOP_SITE), branch(STOP_SITE, blk.labels['lv_stop']),
            'display direct input stop: record live view stopped', expect=struct.pack('<I', START_WORD))
    img.put(off(OSIZE_SITE), branch(OSIZE_SITE, blk.labels['osize']),
            'overlay layer size store: record overlay width and height', expect=struct.pack('<I', OSIZE_WORD))
    img.put(off(PRESENT_SITE), branch(PRESENT_SITE, blk.labels['present'], link=True),
            'overlay bank request: paint 65:24 bars first', expect=branch(PRESENT_SITE, F_PRESENT, link=True))
    listing = blk.listing(Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN))
    raws = {0x12000002: 'and w2, w0, #1', 0x385FE109: 'ldurb w9, [x8, #-2]', 0x381FE149: 'sturb w9, [x10, #-2]',
            COPY_WORD: 'ldp x10, x8, [sp]', **blk.bars_raws}
    for x, w, t in listing:
        word = int.from_bytes(bytes.fromhex(w), 'little')
        require(word not in raws or t == raws[word], f'Bad raw encoding at {x:#x}: {t}')
    return img, dict(prototype=True, milestone=3, label_id=label_id, flag=hex(FLAG), item=hex(ITEM_XPAN),
                     routine=hex(va(CODE_AT)), page=hex(va(page_at)), labels={k: hex(v) for k, v in blk.labels.items()},
                     code=[dict(va=hex(x), word=w, text=t) for x, w, t in listing])
