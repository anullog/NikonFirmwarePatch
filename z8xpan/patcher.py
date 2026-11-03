"""XPan image area (prototype, milestone 1): a sixth "Choose image area" row, XPan (36x13).

XPan runs as the stock 16:9 image area (DS 07000603h = 4) plus a flag, so sensor readout, AF and every
consumer of the image-area value keep their stock 16:9 behaviour. Milestone 1 adds only the menu row:

  page      photo "Choose image area" (GUI state 2Ah): five stock rows FX, DX, 5:4 (hidden), 1:1, 16:9
            plus XPan, a copy of the 16:9 row with its own label and item 35BBh
  flag      byte A03D482C2, the padding between DS 07000602h (4 bytes at A03D482BE) and 07000603h
            (A03D482C4) in the photo-bank group; 1 = XPan, meaningful only while the image area is 16:9
  rows OK   every stock row clears the flag, then runs its stock setter; XPan sets the flag and 16:9
  entry     cursor and mark follow the stock value mapping, XPan when 16:9 and the flag is set
  enable    XPan is available exactly when 16:9 is (stock condition 1Bh)
  banks     the per-item slot copy (410161F0), 32-bit case at 410162C4, also copies the flag byte with
            07000603h, so each photo shooting menu bank keeps its own choice
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
    blk.label('ok_xpan')
    blk.mov_x64(16, FLAG)
    blk.mov_w(17, 1)
    blk.strb_w(17, 16)
    blk.mov_w(0, ITEM_ID & 0xFFFF)
    blk.movk_w16(0, ITEM_ID >> 16)
    blk.mov_w(1, VALUE_169)
    blk.b(F_SET)
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
    return blk


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
    d = blk.labels['copy'] - COPY_SITE
    img.put(off(COPY_SITE), struct.pack('<I', 0x14000000 | ((d // 4) & 0x3FFFFFF)),
            'slot copy, 32-bit case: hook for the XPan flag byte', expect=struct.pack('<I', COPY_WORD))
    listing = blk.listing(Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN))
    raws = {0x12000002: 'and w2, w0, #1', 0x385FE109: 'ldurb w9, [x8, #-2]', 0x381FE149: 'sturb w9, [x10, #-2]',
            COPY_WORD: 'ldp x10, x8, [sp]'}
    for x, w, t in listing:
        word = int.from_bytes(bytes.fromhex(w), 'little')
        require(word not in raws or t == raws[word], f'Bad raw encoding at {x:#x}: {t}')
    return img, dict(prototype=True, milestone=1, label_id=label_id, flag=hex(FLAG), item=hex(ITEM_XPAN),
                     routine=hex(va(CODE_AT)), page=hex(va(page_at)), labels={k: hex(v) for k, v in blk.labels.items()},
                     code=[dict(va=hex(x), word=w, text=t) for x, w, t in listing])
