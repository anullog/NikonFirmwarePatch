"""XPan image area (prototype, milestones 1-5): a sixth "Choose image area" row, XPan (36x13), 65:24 bars,
65:24 still sizes and AF grids, and an XPan icon.

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

  state     zero-initialized block at 408CFF00, written through the text section's RAM alias A0F800000:
            +1 sync active (the tables hold 65:24 values), +2 Fn+dial pending frames, +3 i-menu saved flag,
            +4 sync lock, +8 defer u16, +C last busy u32, +10 unit 1C8h, +20 HDMI; per unit +0 live view
            running (402AA36C / 402AA498, direct input start/stop), +1 clean-up frames left, +8 overlay width,
            +A height (the size register the firmware programs at 402AD198)
  present   before the bank request, with XPan active, live view running and the 16:9 picture shown
            (A032CFC50+168h = 4; not, say, a DX lens), every overlay pixel the GUI left transparent in rows
            0..151 and 530..681 becomes opaque black; icons stay. The 65:24 window is rows 152..529, the
            centre 378 rows of the 576-row 16:9 picture. Cache lines are cleaned and invalidated around the
            CPU writes. Once XPan ends, the next 12 frames of that unit clean the marked pixels back to
            transparent (clean-up); marks are alpha FEh (bars) and FDh (GUI edges composited over black).
Unit 15484000h also drives the EVF (1280x960 overlay; A02810470 says which output is shown). HDMI is unit
15480000h, presented by 402C33FC with its bank request (402C3A58) hooked the same way. Milestones 3-4: a sync
routine on both hooks swaps the 16:9 still sizes and AF grid descriptors to 65:24 values while XPan is active
(flag, 16:9, photo mode), see sync() and af_swaps(). Icon: image ids 1862h/1863h (XPan) come from new records
through a hook in the image lookup; the live view and Image area row pickers choose them with the flag set, see
icons(). Fn + command dial and the i menu offer XPan after 16:9, see dial() and imenu().
NEF/JPEG: the MakerNote CropArea writer records the 65:24 window while XPan is active, see nef(); raw data
stays the 16:9 readout. Playback is untouched.
Used by features.compose; offline only.
"""
import hashlib
import re
import struct
import zlib

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from z8prores.a64 import Block
from z8prores.menu import BLOB_DIR, F_ENTRY_GUARD, F_SET_CURSOR, F_SET_MARK, Image, TABLE_DIR
from z8prores.package import require

BASE, DATA_OFF, DATA_VA = 0x40160000, 0x4B13000, 0xA02808000
CODE_AT, GUI_AT, AREA_END = 0x766000, 0x767800, 0x770000     # free text gap below the shared strings, see build_main
STATE = 0x4C00C80                                            # GUI state 2Ah record (data image)
STOCK_CALLBACKS, STOCK_PAGE = 0xA028E6E40, 0xA028CC9D0
CALLBACKS = (0x405DBE0C, 0x405DBEA4, 0x403B2210, 0x405DBCD0, 0)
ROWS, EVENTS = 0x43DD2A8C, 0x43DD2B68
LABELS = (0x533, 0x30C, 0x43A, 0x534, 0x582)                 # FX, DX, 5:4, 1:1, 16:9
ITEMS = (0x2BEB, 0x29B8, 0x2BE9, 0x2BEA, 0x2C5F)
STUBS = (0x405E69C0, 0x405E69F0, 0x405E69D0, 0x405E69E0, 0x405E6A10)   # SetDS(07000603h, 0/1/2/8/4)
OK_EVENTS, END_EVENT = (0x286, 0x28B, 0x2AE), 955
ITEM_XPAN = 0x35BB                                           # unused item id (35B5..35BA: ProRes, N-RAW)
ITEM_ID, VALUE_169 = 0x07000603, 4
FLAG = 0xA03D482C2
AREA = FLAG + 2                                              # DS 07000603h, the image area value itself
COPY_SITE, COPY_NEXT, COPY_WORD = 0x410162C4, 0x410162D0, 0xA94023EA   # ldp x10,x8,[sp]; then ldr/str w9
F_GET, F_SET = 0x403EC398, 0x403EC39C
F_ROW, F_ROW_STATE = 0x403AF870, 0x403B1ED0
F_CONDITION, F_ENABLE = 0x403A036C, 0x403B1A5C
COND_BLOCKED, COND_169 = 0x19, 0x1B
# sha256 of stock code the patch replaces or relies on: the entry and enable callbacks, the SetDS stubs, then the
# displaced words and the registers/stack slots each hook stub expects around its site (tagged by stub label)
PINNED = {0x405DBE0C: (0x98, '6e08bf0521ed62638837f6f84730b9880fe8af8c3bb851ea1b9934473a9a2803'),   # entry
          0x405DBCD0: (0x7C, '9b771967eee754f7e6a8820bc7f6167a3132a55230bb67183572fcb79066b4ae'),   # enable
          0x405E69C0: (0x60, '84dbd5338c987625ad3d97a3c6201140ad886e1f95bf30dc1887217bea563d02'),   # ok_* stubs
          0x402CF9A0: (0x34, '9124bea6573e0116378b4104ccf6cb947e9c852443649c5383bc8dac7e2b3fbf'),   # present
          0x402C3A1C: (0x40, 'd0f29cccc95ee64f35999902c5fdfb6966f350ad61abc6bc7595e0e03c13d7c5'),   # hdmi_present
          0x406E9BF4: (0x0C, '3464212b528205430576d548cf9e51bed8162ec902703d9d38eb392aa1fcceeb'),   # croparea
          0x4032DC94: (0x14, '7b8eff724cb53f21d48de8c37322c283e515dbf79d8e250f47a9dfec136416ee'),   # img_lookup
          0x40D4E82C: (0x18, '15ce167edb820070ff26c5699c064e78eeeec82bdf6480f59a475aedc04ff217'),   # dial table
          0x40D3A7B4: (0x14, '1e765480f515a5975133b0bdd767381e8c08eac689f8ef387e31b923c18b8ff7'),   # dial_flags
          0x40D497CC: (0x14, '46990de893ef3e2cce946459698e245dd1aba75f6c65641c5fa86de22cec8ea5'),   # dial_cur
          0x40D4A37C: (0x14, 'fe693b827729deeac9cd709b193f0393c3b13abc7dfca2ec5831d320b506ad04'),   # dial_set
          0x4049AC3C: (0x14, '74c1780488f496c9aaa28d150f056556e232015cc01ec5e260f5da1bcaccd83e'),   # fn_popup
          0x404EA13C: (0x14, 'e7b47510eb1700d6160cf9da3b4604d430a89d3ce616eff60b631c0a5f0bd480'),   # imenu_popup
          0x4041A9DC: (0x14, 'b1b83deb4a3b6d5281742d5d7fc4d7e191644e696c1e58e50ce00017c22db471'),   # lv_icon
          0x405DBD98: (0x14, 'e4a788d68a865f83fc43fc07ea31d252f030cb8739bb357f1041e263d8a7f293'),   # menu_icon0
          0x405D9A14: (0x14, 'd561c8db33cf82908cdde3439f227d8113f2cae116fb3b40402beda4c69a47b6')}   # menu_icon1
STOCK_LABEL = 0x582                                          # "16:9 (36×20)" and its 35 translations
# milestone 2: opaque bars painted into a display's overlay just before each frame is presented
LCD_BASE, HDMI_BASE = 0x15484000, 0x15480000                 # display units 1C8h (rear monitor + EVF), 1C7h (HDMI)
START_SITE, START_NEXT = 0x402AA36C, 0x402AA370              # str w9,[x8,#0x100]: direct input start
STOP_SITE, STOP_NEXT = 0x402AA498, 0x402AA49C                # str w9,[x8,#0x100]: direct input stop
START_WORD = 0xB9010109
PRESENT_SITE, F_PRESENT = 0x402CF9D0, 0x402AB1CC             # bl: bank request after the overlay address
OSIZE_SITE, OSIZE_NEXT, OSIZE_WORD = 0x402AD198, 0x402AD19C, 0xB92401BB   # str w27,[x13,#0x2400]: overlay size
STATE_AT, STATE_SIZE = 0x76FF00, 0x30                        # zero-initialized runtime state, layout in the docstring
DISPLAY_SEL, PANELS = 0xA02810470, ((1024, 682), (1280, 960)) # unit 1C8h output shown: 0 rear monitor, 1 EVF
# HDMI present (402C33FC): bl 402AB1CC with x0 = unit 1C7h, w1 = layer, colour CPU address at [sp+0xA8]; the
# surface record x20 + x26 * x21 holds +18h colour and +20h alpha plane, [x20] the pixel type (14h: YYUV + alpha)
HDMI_SITE, HDMI_SIZES, SURFACE_YYUVA = 0x402C3A58, ((1920, 1080), (1280, 720)), 0x14
SURFACE_RECORD, HEIGHT_BITS = 0x48, 14                       # surface records of 48h bytes; size register: h in 14 bits
EFFECTIVE_AREA = 0xA032CFC50 + 0x168                          # live-view image-area icon index shown (4 = 16:9)
DIRTY_FRAMES = 12                                            # per unit +1: frames left to clean after XPan ends
MARK, MARK_EDGE = 0xFE, 0xFD                                  # painted alpha: bars (black), composited GUI edges
LDAXRB, STXRB, STLRB, CLREX = 0x085FFD07, 0x08067D07, 0x089FFD1F, 0xD5033F5F  # sync lock at x8 (w7, w6)
DMB_ISH = 0xD5033BBF
# still pipeline: u32 count of jobs in flight (+1 at job creation 40F74E30, -1 when the job's info is freed
# 40F6FB9C). Later stages re-read the size table by index, so a table switch waits until it is 0.
PIPE_BUSY, DEFER_FRAMES = 0xA03BC5FA0, 1800                  # u16 frames the count stayed
# NEF/JPEG MakerNote 0045h CropArea (left, top, width, height), written by 406E9B90 from info+280h; info+26Ch is
# the image area. Raw data stays the 16:9 readout; XPan records the 65:24 window inside it.
CROP_SITE, CROP_NEXT, CROP_WORD = 0x406E9BFC, 0x406E9C00, 0x79000EC8   # strh w8,[x22,#6]: height (w9 width)
LDUR_AREA = 0xB85EC2AA                                        # ldur w10,[x21,#-0x14]: info+26Ch
TEXT_SECTION, TEXT_ALIAS = 0x40200000, 0xA0F800000           # writable RAM alias of the copied text section
RAM_LO, RAM_HI = 0xA00000000, 0xB00000000                    # 402AB038 converts to the bus address itself
BLACK_PAIR = 0x8080000080800000                              # Y0 Y1 U V; the overlay conversion is full range
# milestone 3: 16:9 entries of the still-size table (RAM data, read through 41061928) and their 65:24 values;
# raw readout (8280x4656) is left at 16:9. Heights are multiples of 8 (160x60: of 4).
SIZES = ((0xA02A94744, (8256, 4640), (8256, 3048)), (0xA02A94748, (6192, 3480), (6192, 2288)),
         (0xA02A9474C, (4128, 2320), (4128, 1528)), (0xA02A94970, (8256, 4640), (8256, 3048)),
         (0xA02A94974, (8256, 4640), (8256, 3048)), (0xA02A94978, (8256, 4640), (8256, 3048)),
         (0xA02A947E0, (640, 360), (640, 240)), (0xA02A947F4, (320, 180), (320, 120)),
         (0xA02A94808, (1920, 1080), (1920, 712)), (0xA02A9481C, (160, 90), (160, 60)))
CROP_169, CROP_XPAN_H = SIZES[0][1], SIZES[0][2][1]          # the full 16:9 still and its 65:24 height
SWAP_TABLE_AT = 0x769000                                     # read-only {address, stock, XPan} words for sync
SELECTOR = 0xA03D7C94C                                       # DS 0218Ch photo/video selector (1 = video)
# milestone 4: AF grid descriptors in RAM data, read only by 40F3493C (memcpy of 0x1C bytes per call): tables of
# 0xFC-byte rows (9 image areas each), one row per AF-area mode or per custom wide-area size. Descriptor u16s:
# extent x0,y0,x1,y1 (sensor), box w,h, 16,16, step x,y, columns,0, rows,0; rows = (y1 - y0 - h) / step y + 1.
AF_MODE, AF_AREA = 0xFC, 0x1C
AF_TABLES = ((0xA02A5CE38, 9), (0xA02A5D714, 9), (0xA02A4C470, 135), (0xA02A54954, 135))
BAND_169, BAND_XPAN = (432, 5072), (1228, 4276)              # sensor rows of the 16:9 and 65:24 pictures
# XPan icon: two new grey8 PNG images, the stock 16:9 frame with "XPAN" text, served by the image lookup 4032DC64
# for ids past the stock table (6242 records at 431D96D0; record = table + 24 * id: u32 flags, u32 size, u64 0,
# u64 PNG). Live view/EVF and the i-menu tile use the 36x32 image, menus the 36x36 one.
IMAGE_TABLE, IMAGE_COUNT, IMAGE_FLAGS = 0x431D96D0, 0x1862, 0x100
IMAGE_BASE_SITE = 0x403EE56C                                  # boot: mov x0,#table; bl 4032DC34 (base -> A02F2AB80)
ICON_LV, ICON_MENU = IMAGE_COUNT, IMAGE_COUNT + 1             # new ids; stock 16:9 counterparts 84Bh, 1173h
STOCK_LV, STOCK_MENU = 0x84B, 0x1173
ICON_AT = 0x76C000                                            # two image records, then their PNGs
LOOKUP_SITE, LOOKUP_NEXT, LOOKUP_WORD = 0x4032DCA0, 0x4032DCA4, 0xF9400129     # ldr x9,[x9]: table base, w8 = id
LV_ICON_SITE, LV_ICON_NEXT, LV_ICON_WORD = 0x4041A9E4, 0x4041A9E8, 0x528004A0  # mov w0,#0x25; w3 = image-area icon
MENU_ICON_SITES = ((0x405DBDA0, 0x405DBDAC, 21, 0x52822E75),  # Image area row summary icons (widget 29B7h):
                   (0x405D9A1C, 0x405D9A28, 1, 0x52822E61))   # mov wN,#0x1173 then b NEXT
IMENU_ICON = 0xA02870848                                      # i-menu image-area tile: 16:9 image selector id
IMENU_LABEL = 0xA028760A8                                     # i-menu image-area list: 16:9 label (582h)
# Fn + command dial (control side, 40D49780): rows {value, 0, 7FFFFFFFh, allowed byte at +C} at A02823428, shared by
# photo and video, returned by 40D4E82C. XPan becomes an eighth row after 16:9 in a writable copy; picking it writes
# 16:9 and leaves a pending byte that sync turns into the flag once the image area reads 16:9.
DIAL_TABLE, DIAL_VALUES, DIAL_XPAN = 0xA02823428, (0, 3, 1, 2, 8, 4, 5), 0x40
DIAL_ROW_169, DIAL_ROW_XPAN, DIAL_TABLE_AT = 5, 6, 0x76D000
DIAL_COUNT_SITE, DIAL_COUNT_WORD, DIAL_ADDR_SITE = 0x40D4E830, 0x32000BE8, 0x40D4E838   # mov w8,#7; mov x0,#table
DIAL_CURSOR_PHOTO = 0xA0321F100                               # current row (photo; video uses A0321F104)
DIAL_FLAGS_SITE, DIAL_FLAGS_NEXT, DIAL_FLAGS_WORD = 0x40D3A7BC, 0x40D3A7C0, 0xA94C7BF3  # ldp x19,x30 after 40D4702C
DIAL_CUR_SITE, DIAL_CUR_NEXT, DIAL_CUR_WORD = 0x40D497D4, 0x40D497D8, 0xB9400338        # ldr w24,[x25]
DIAL_SET_SITE, DIAL_SET_NEXT, DIAL_SET_WORD = 0x40D4A384, 0x40D4A388, 0xB9400109        # ldr w9,[x8]: photo value
FN_POPUP_SITE, FN_POPUP_NEXT, FN_POPUP_WORD = 0x4049AC44, 0x4049AC48, 0x5280B04E        # mov w14,#0x582 (w12 = 84Bh)
PENDING_FRAMES = 200                                          # frames a Fn+dial XPan pick waits for 16:9 to land
# i menu, image-area item descriptor 43A1D408: +10 dial step, +20 list open, +30 rollback. XPan is the dial step
# after 16:9; the list keeps four rows and its 16:9 row shows XPan while XPan is current (sync swaps its icon, label).
IMENU_DESC, IMENU_STEP, IMENU_OPEN, IMENU_ROLLBACK = 0x43A1D408, 0x404EA570, 0x404D907C, 0x404DB3BC
IMENU_COMMIT, TRANSACTION = 0x404DB3B8, 0xA032BBD5C             # +28 commit; DS transaction open byte
IMENU_APPLY_SITE, F_APPLY = 0x404D948C, 0x403EC4D0            # b 403EC4D0: list cursor applies w1 to item w0
IMENU_POPUP_SITE, IMENU_POPUP_NEXT, IMENU_POPUP_WORD = 0x404EA144, 0x404EA148, 0x7829796C  # strh w12,[x11,x9,lsl #1]
XPAN_TEXT = (                                                 # 30x14 at (3, 7): '#' 255, '+' 160, ':' 80
    '##...##.#####+...###...##...##', '##...##.######...###...###..##',
    '+#+.+#+.##..##..+#+#+..###..##', '.##.##..##..##..##.##..###:.##',
    '.+#+#+..##..##..##.##..##+#.##', '..###...##..##.+#+.+#+.##:#:##',
    '..+#+...######.##...##.##.#+##', '..+#+...#####+.##...##.##.+###',
    '..###...##.....##...##.##.:###', '.+#+#+..##.....#######.##..###',
    '.##.##..##.....#######.##..###', '+#+.+#+.##.....##...##.##...##',
    '##...##.##.....##...##.##...##', '##...##.##.....##...##.##...##')


def ram(address):
    return address - TEXT_SECTION + TEXT_ALIAS


def va(offset):
    return BASE + offset


def off(address):
    return address - DATA_VA + DATA_OFF if address >= DATA_VA else address - BASE


# runtime state and the dial table copy, as the code addresses them (text section RAM alias)
STATE_RAM = ram(va(STATE_AT))
ACTIVE, PENDING, SAVED, LOCK = STATE_RAM + 1, STATE_RAM + 2, STATE_RAM + 3, STATE_RAM + 4   # SAVED: bit 7 armed, 0 flag
DEFER, LAST_BUSY, LCD_STATE, HDMI_STATE = STATE_RAM + 8, STATE_RAM + 0xC, STATE_RAM + 0x10, STATE_RAM + 0x20
DIAL_TABLE_RAM = ram(va(DIAL_TABLE_AT))
RAWS = {}                                                     # hand-encoded word -> its disassembly, see build_main


def raw(blk, word, text):
    """A word the assembler has no form for: a replayed stock word (the displaced one, deliberately exact) or an
    exclusive/barrier instruction. build_main checks Capstone reads it as `text`."""
    RAWS[word] = text
    blk.raw(word)


def branch(at, to, link=False):
    d = to - at
    require(d % 4 == 0 and -(1 << 27) <= d < (1 << 27), 'Branch target out of range')
    return struct.pack('<I', (0x94000000 if link else 0x14000000) | ((d // 4) & 0x3FFFFFF))


def mov_x0(value):
    """mov x0,#value as movz + three movk, the firmware's own form."""
    return struct.pack('<4I', *((0xF2800000 if i else 0xD2800000) | i << 21 | (value >> 16 * i & 0xFFFF) << 5
                                for i in range(4)))


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


def load_flag(blk, rd):
    """wRD = XPan flag (x16 scratch)."""
    blk.mov_x64(16, FLAG)
    blk.ldrb_w(rd, 16)


def store_flag(blk, value):
    """XPan flag = value (x16 scratch, w17 for a set)."""
    blk.mov_x64(16, FLAG)
    if value:
        blk.mov_w(17, value)
    blk.strb_w(17 if value else 31, 16)


def code(count, label_id):
    """The routine at CODE_AT: OK wrappers of the five stock rows and XPan's, the flag clear, the entry and enable
    callbacks, the bank slot-copy hook, then the icon hooks (icons), the CropArea hook (nef), the Fn+dial hooks
    (dial), the i-menu hooks (imenu), the live-view and present hooks with the bar painter (bars) and the table
    sync (sync)."""
    blk = Block(va(CODE_AT))
    # OK actions: stock rows clear the flag first; XPan stores 16:9 with the flag set
    for name, stub in zip(('ok_fx', 'ok_dx', 'ok_54', 'ok_11', 'ok_169'), STUBS):
        blk.label(name)
        blk.mov_x(17, 30)
        blk.bl('clear_xpan')
        blk.mov_x(30, 17)
        blk.b(stub)
    blk.label('clear_xpan')                     # no XPan, and no Fn+dial XPan pick still waiting (x16 only)
    store_flag(blk, 0)
    blk.mov_x64(16, PENDING)
    blk.strb_w(31, 16)
    blk.ret()
    blk.label('ok_xpan')                        # 16:9 first, then the flag: sync clears a flag without 16:9
    blk.stp_pre(19, 30, -16)
    blk.mov_w32(0, ITEM_ID)
    blk.mov_w(1, VALUE_169)
    blk.bl(F_SET)
    store_flag(blk, 1)
    blk.ldp_post(19, 30, 16)
    blk.ret()
    # entry: stock 405DBE0C with the XPan case added to the value-to-row mapping
    blk.label('entry')
    blk.stp_pre(19, 30, -32)
    blk.stp(20, 21, 16)
    blk.mov_x(19, 0)
    blk.mov_w32(0, ITEM_ID)
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
    load_flag(blk, 16)
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
    blk.and_w_mask(2, 0, 1)
    blk.mov_w(1, ITEM_XPAN)
    blk.mov_x(0, 19)
    blk.bl(F_ENABLE)
    blk.label('enable_done')
    blk.mov_wr(0, 20)
    blk.ldp(20, 21, 16)
    blk.ldp_post(19, 30, 32)
    blk.ret()
    # slot copy, 32-bit case: carry the flag byte with 07000603h (x8 source, x10 destination)
    blk.label('copy')                           # the three displaced stock words first
    raw(blk, COPY_WORD, 'ldp x10, x8, [sp]')
    raw(blk, 0xB9400109, 'ldr w9, [x8]')        # the stock copy first, so the flag never leads the area
    raw(blk, 0xB9000149, 'str w9, [x10]')
    blk.lsl_wi(16, 20, 16)
    blk.mov_w(17, 0)
    blk.movk_w16(17, ITEM_ID & 0xFFFF)
    blk.cmp_w(16, 17)
    blk.b_cond('ne', 'copy_done')
    blk.ldurb(16, 8, -2)
    blk.sturb(16, 10, -2)
    blk.label('copy_done')
    blk.b(COPY_NEXT)
    icons(blk)
    nef(blk)
    dial(blk, label_id)
    imenu(blk, label_id)
    bars(blk)
    sync(blk, count)
    return blk


def bars(blk):
    """Milestones 2, 2b and 5: 65:24 bars in live view on the rear monitor, the EVF and HDMI, cleaned up after.

    Per display unit at LCD_STATE / HDMI_STATE: +0 live view running, +1 frames left to clean, +8/+A overlay
    width and height, each recorded where the firmware programs them. The recorded size is the previous frame's,
    so a frame is painted only when it matches the buffer being presented: on unit 1C8h the size of the output
    A02810470 selects, on HDMI the surface's own plane addresses. The 16:9 picture is centred on the panel (rear
    monitor: rows 53..628 of 682).
    """
    def unit_state(base_reg, scratch, lcd_label, hdmi_label, none_label):
        """Branch to lcd_label or hdmi_label by display unit; flags are left untouched."""
        blk.mov_x64(scratch, LCD_BASE)
        blk.sub_x(scratch, base_reg, scratch)
        blk.cbz_x(scratch, lcd_label)
        blk.mov_x64(scratch, HDMI_BASE)
        blk.sub_x(scratch, base_reg, scratch)
        blk.cbz_x(scratch, hdmi_label)
        blk.b(none_label)

    # direct input (live view) started / stopped, per unit
    for name, value, nxt in (('lv_start', 1, START_NEXT), ('lv_stop', 0, STOP_NEXT)):
        blk.label(name)
        raw(blk, START_WORD, 'str w9, [x8, #0x100]')
        unit_state(8, 16, name + '_lcd', name + '_hdmi', name + '_done')
        blk.label(name + '_lcd')
        blk.mov_x64(16, LCD_STATE)
        blk.b(name + '_set')
        blk.label(name + '_hdmi')
        blk.mov_x64(16, HDMI_STATE)
        blk.label(name + '_set')
        blk.mov_w(17, value)
        blk.strb_w(17, 16)
        blk.label(name + '_done')
        blk.b(nxt)
    # overlay size store (layer 0): only x17 is free here and the flags of a preceding tst are still live
    blk.label('osize')
    raw(blk, OSIZE_WORD, 'str w27, [x13, #0x2400]')
    blk.mov_x64(17, LCD_BASE)
    blk.sub_x(17, 13, 17)
    blk.cbnz_x(17, 'osize_hdmi')
    blk.mov_x64(17, LCD_STATE + 8)
    blk.str_w(27, 17)
    blk.b('osize_done')
    blk.label('osize_hdmi')
    blk.mov_x64(17, HDMI_BASE)
    blk.sub_x(17, 13, 17)
    blk.cbnz_x(17, 'osize_done')
    blk.mov_x64(17, HDMI_STATE + 8)
    blk.str_w(27, 17)
    blk.label('osize_done')
    blk.b(OSIZE_NEXT)

    def present(name, base, unit, layers, sizes, selector):
        """Bank-request hook of one display unit: keep the tables in step with XPan (sync), then, with live view
        running on `base`, the overlay (layer in `layers`) and its recorded size one of `sizes`, fall through with
        x11 = unit state, w14 x w13 the frame and w7 = 1: paint (XPan active, 16:9 shown) or 0: clean up this
        unit's earlier bars; anything else presents unchanged. With `selector` (the output shown now) the size
        must be the one indexed by it."""
        blk.label(name)
        blk.mov_x(15, 30)
        blk.bl('sync')
        blk.mov_x(30, 15)
        for layer in layers[:-1]:
            blk.cmp_wi(1, layer)
            blk.b_cond('eq', name + '_layer')
        blk.cmp_wi(1, layers[-1])
        blk.b_cond('ne', 'flip')
        if len(layers) > 1:
            blk.label(name + '_layer')
        blk.ldr_x(9, 0, 8)
        blk.mov_x64(16, base)
        blk.cmp_x(9, 16)
        blk.b_cond('ne', 'flip')                # another unit presents here
        blk.mov_x64(11, unit)
        blk.ldrb_w(17, 11)
        blk.cbz_w(17, 'flip')                   # live view not running on this unit
        blk.mov_x64(16, ACTIVE)
        blk.ldrb_w(7, 16)
        blk.cbz_w(7, name + '_dirty')
        blk.mov_x64(16, EFFECTIVE_AREA)
        blk.ldrb_w(16, 16)
        blk.cmp_wi(16, VALUE_169)
        blk.b_cond('eq', name + '_want')
        blk.mov_w(7, 0)                         # e.g. a DX lens: the picture isn't 16:9
        blk.label(name + '_dirty')
        blk.ldrb_w(16, 11, 1)
        blk.cbz_w(16, 'flip')                   # nothing of ours left to clean
        blk.label(name + '_want')
        blk.ldrh_w(14, 11, 8)                   # overlay width and height as last programmed
        blk.ldrh_w(13, 11, 10)
        blk.and_w_mask(13, 13, HEIGHT_BITS)     # bits 30-31 of the size register are flags
        if selector:
            blk.mov_x64(16, selector)
            blk.ldr_w(16, 16)
        for n, (w, h) in enumerate(sizes):
            if selector:                        # must be the size of the output shown now
                blk.cmp_wi(16, n)
                blk.b_cond('ne', f'{name}_size{n}_next')
                blk.cmp_wi(14, w)
                blk.b_cond('ne', 'flip')
                blk.cmp_wi(13, h)
                blk.b_cond('ne', 'flip')
                blk.b(name + '_size_ok')
            else:
                blk.cmp_wi(14, w)
                blk.b_cond('ne', f'{name}_size{n}_next')
                blk.cmp_wi(13, h)
                blk.b_cond('eq', name + '_size_ok')
            blk.label(f'{name}_size{n}_next')
        blk.b('flip')
        blk.label(name + '_size_ok')

    def surface(rec_index, rec_base, colour_slot):
        """x9 = surface record; require YYUV+alpha, x4 = colour at its start, x3 = alpha right after w14 x w13."""
        blk.ldr_w(9, rec_base)
        blk.cmp_wi(9, SURFACE_YYUVA)
        blk.b_cond('ne', 'flip')
        blk.mov_w(16, SURFACE_RECORD)
        blk.madd_x(9, rec_index, 16, rec_base)
        blk.ldr_x(4, 31, colour_slot)           # colour CPU address handed to 402AB038 (bus = +400000000h)
        blk.ldr_x(12, 9, 0x18)
        blk.cmp_x(4, 12)
        blk.b_cond('ne', 'flip')                # window offset: not a full-surface present
        blk.umull(12, 14, 13)
        blk.add_x(3, 4, 12, lsl=1)
        blk.ldr_x(12, 9, 0x20)
        blk.cmp_x(3, 12)
        blk.b_cond('ne', 'flip')                # plane layout differs from the recorded size
        blk.b('frame_mode')

    # bank request of the overlay (402CF9D0): unit 1C8h, layer 2 (rear monitor) or 3 (EVF), the shown output's size
    present('present', LCD_BASE, LCD_STATE, (2, 3), PANELS, DISPLAY_SEL)
    surface(23, 24, 0x28)                       # x24 surface descriptor, x23 record index (402CF9A0)
    # HDMI bank request (402C3A58): unit 1C7h, layer 2, sizes in HDMI_SIZES only
    present('hdmi_present', HDMI_BASE, HDMI_STATE, (2,), HDMI_SIZES, None)
    surface(26, 20, 0xA8)                       # x20 surface descriptor, x26 record (x21 = 48h, 402C3A1C)
    # verified frame w14 x w13 (x4 colour, x3 alpha): paint, or clean up and count down
    blk.label('frame_mode')                     # x11 = this unit's state: +1 frames left to clean
    blk.cbz_w(7, 'frame_clean_count')
    blk.mov_w(17, DIRTY_FRAMES)
    blk.strb_w(17, 11, 1)
    blk.b('frame_paint')
    blk.label('frame_clean_count')
    blk.ldrb_w(17, 11, 1)
    blk.subs_wi(17, 17, 1)
    blk.strb_w(17, 11, 1)
    blk.b('frame_clean')
    for kind in ('paint', 'clean'):
        blk.label('frame_' + kind)
        blk.add_w(8, 14, 14, lsl=3)
        blk.lsr_wi(8, 8, 4)                     # 16:9 picture height = width * 9 / 16, centred on the panel
        blk.cmp_w(8, 13)
        blk.b_cond('hi', 'flip')
        blk.sub_w(9, 13, 8)
        blk.lsr_wi(9, 9, 1)                     # 16:9 picture top
        blk.lsl_wi(10, 14, 3)
        blk.add_w(10, 10, 10, lsl=1)
        blk.mov_w(16, 65)
        blk.udiv_w(10, 10, 16)
        blk.lsr_wi(10, 10, 1)
        blk.lsl_wi(10, 10, 1)                   # 65:24 height = width * 24 / 65, even
        blk.sub_w(16, 8, 10)
        blk.lsr_wi(16, 16, 1)
        blk.add_w(16, 16, 9)                    # first 65:24 row
        blk.add_w(17, 16, 10)                   # first row below the 65:24 window
        blk.mov_x64(12, RAM_LO)
        blk.cmp_x(4, 12)
        blk.b_cond('lo', 'flip')
        blk.mov_x64(12, RAM_HI - 0x800000)
        blk.cmp_x(4, 12)
        blk.b_cond('hs', 'flip')
        blk.mov_x(15, 30)
        blk.stp_pre(17, 13, -16)
        blk.mov_w(5, 0)
        blk.mov_wr(6, 16)
        blk.bl(kind)
        blk.ldp_post(5, 6, 16)
        blk.bl(kind)
        blk.dsb_sy()
        blk.mov_x(30, 15)
        blk.b('flip')
    blk.label('flip')
    blk.b(F_PRESENT)

    def rows(kind, group):
        """Rows [w5, w6) of width x14 (x3 alpha plane, x4 YYUV plane), group() per 8 pixels at x9; cache
        lines are invalidated before reading and cleaned after writing."""
        blk.label(kind)
        blk.label(kind + '_row')
        blk.cmp_w(5, 6)
        blk.b_cond('hs', kind + '_done')
        blk.madd_x(7, 5, 14, 3)
        blk.madd_x(8, 5, 14, 31)
        blk.add_x(8, 4, 8, lsl=1)
        for phase in ('inv', 'cln'):
            if phase == 'cln':
                blk.label(kind + '_px_done')
            blk.mov_w(9, 0)
            blk.label(kind + '_' + phase)
            blk.add_x(10, 7, 9)
            blk.dc_civac(10)
            blk.add_x(11, 8, 9, lsl=1)
            blk.dc_civac(11)
            blk.add_xi(11, 11, 64)
            blk.dc_civac(11)
            blk.add_xi(9, 9, 64)
            blk.cmp_x(9, 14)
            blk.b_cond('lo', kind + '_' + phase)
            blk.dsb_sy()
            if phase == 'inv':
                blk.mov_w(9, 0)
                blk.mov_x64(11, MARK * 0x0101010101010101)
                blk.label(kind + '_px')
                blk.ldr_x_idx(10, 7, 9)
                group()
                blk.label(kind + '_next')
                blk.add_xi(9, 9, 8)
                blk.cmp_x(9, 14)
                blk.b_cond('lo', kind + '_px')
                blk.b(kind + '_px_done')
        blk.add_xi(5, 5, 1)
        blk.b(kind + '_row')
        blk.label(kind + '_done')
        blk.ret()

    def paint_group():
        """Transparent pixels become marked black; drawn pixels (icons) stay."""
        blk.cbz_x(10, 'paint_fill')
        blk.cmn_xi(10, 1)
        blk.b_cond('eq', 'paint_next')
        blk.cmp_x(10, 11)
        blk.b_cond('eq', 'paint_next')          # already ours
        blk.mov_w(12, 0)
        blk.label('paint_pair')                 # pixels p, p+1 share U and V
        blk.add_x(13, 9, 12)
        blk.ldrh_idx(10, 7, 13)
        blk.add_x(16, 8, 13, lsl=1)             # Y of pixel p (p even); Y p+1, U, V follow
        blk.cbnz_w(10, 'paint_mixed')
        blk.mov_w(10, MARK * 0x101)
        blk.strh_idx(10, 7, 13)
        blk.mov_w(17, 0)
        blk.movk_w16(17, 0x8080)
        blk.str_w(17, 16)                       # Y Y U V = 0 0 80h 80h
        blk.b('paint_pair_next')
        blk.label('paint_mixed')                # per pixel: 0 -> marked black; 1..FDh -> composited over black
        for half, (lo, label) in enumerate(((True, 'paint_lo'), (False, 'paint_hi'))):
            if lo:
                blk.and_w_mask(17, 10, 8)
            else:
                blk.lsr_wi(17, 10, 8)
                blk.add_xi(13, 13, 1)
            blk.cbz_w(17, label + '_clear')
            blk.cmp_wi(17, MARK_EDGE)
            blk.b_cond('hs', label + '_done')   # FDh/FEh ours, FFh opaque
            blk.ldrb_w(11, 16, half)
            blk.mul_w(11, 11, 17)
            blk.lsr_wi(11, 11, 8)
            blk.strb_w(11, 16, half)
            blk.mov_w(17, MARK_EDGE)
            blk.strb_idx(17, 7, 13)
            blk.b(label + '_done')
            blk.label(label + '_clear')
            blk.strb_w(31, 16, half)
            blk.mov_w(17, MARK)
            blk.strb_idx(17, 7, 13)
            blk.label(label + '_done')
        blk.label('paint_pair_next')
        blk.add_xi(12, 12, 2)
        blk.cmp_xi(12, 8)
        blk.b_cond('lo', 'paint_pair')
        blk.mov_x64(11, MARK * 0x0101010101010101)  # x11 was a scratch register above
        blk.b('paint_next')
        blk.label('paint_fill')
        blk.str_x_idx(11, 7, 9)
        blk.add_x(12, 8, 9, lsl=1)
        blk.mov_x64(13, BLACK_PAIR)
        blk.str_x(13, 12, 0)
        blk.str_x(13, 12, 8)

    def clean_group():
        """Marked pixels become transparent again."""
        blk.cbz_x(10, 'clean_next')
        blk.cmn_xi(10, 1)
        blk.b_cond('eq', 'clean_next')
        blk.cmp_x(10, 11)
        blk.b_cond('ne', 'clean_mixed')
        blk.str_x_idx(31, 7, 9)
        blk.b('clean_next')
        blk.label('clean_mixed')
        blk.mov_w(12, 0)
        blk.label('clean_byte')
        blk.add_x(13, 9, 12)
        blk.ldrb_idx(10, 7, 13)
        blk.cmp_wi(10, MARK_EDGE)
        blk.b_cond('lo', 'clean_byte_next')
        blk.cmp_wi(10, 255)
        blk.b_cond('eq', 'clean_byte_next')
        blk.strb_idx(31, 7, 13)
        blk.label('clean_byte_next')
        blk.add_xi(12, 12, 1)
        blk.cmp_xi(12, 8)
        blk.b_cond('lo', 'clean_byte')

    rows('paint', paint_group)
    rows('clean', clean_group)


def sync(blk, count):
    """Milestones 3-5, run on every present and i-menu step: settle the i-menu saved flag and a pending Fn+dial
    XPan pick, drop the flag once the image area is no longer 16:9, then while XPan is active (flag, 16:9 and the
    photo/video selector on photo; ACTIVE records it) keep the 16:9 entries of the still-size and AF-grid tables
    and the i-menu tile at their 65:24 values, switching only once the still pipeline has drained."""
    blk.label('sync')                           # x0-x2 and x15 are left alone; another task syncing: skip
    blk.mov_x64(8, LOCK)
    blk.label('sync_lock')
    raw(blk, LDAXRB, 'ldaxrb w7, [x8]')
    blk.cbnz_w(7, 'sync_busy')
    blk.mov_w(7, 1)
    raw(blk, STXRB, 'stxrb w6, w7, [x8]')
    blk.cbnz_w(6, 'sync_lock')
    blk.mov_x64(16, TRANSACTION)                # an i-menu restore point is meaningful only while its DS
    blk.ldrb_w(17, 16)                          # transaction is open
    blk.cmp_wi(17, 1)
    blk.b_cond('eq', 'sync_pending')
    blk.mov_x64(16, SAVED)
    blk.strb_w(31, 16)
    blk.label('sync_pending')
    blk.mov_x64(16, PENDING)                    # Fn+dial picked XPan: set the flag once the area reads 16:9
    blk.ldrb_w(17, 16)
    blk.cbz_w(17, 'sync_flag')
    blk.mov_x64(11, AREA)
    blk.ldr_w(9, 11)
    blk.cmp_wi(9, VALUE_169)
    blk.b_cond('ne', 'sync_wait')
    blk.strb_w(31, 16)
    blk.mov_w(9, 1)
    blk.mov_x64(16, FLAG)
    blk.strb_w(9, 16)
    blk.b('sync_flag')
    blk.label('sync_wait')                      # gives up after PENDING_FRAMES frames
    blk.subs_wi(17, 17, 1)
    blk.strb_w(17, 16)
    blk.label('sync_flag')
    load_flag(blk, 17)
    blk.mov_x64(16, AREA)
    blk.ldr_w(9, 16)
    blk.mov_w(10, 0)
    blk.cbz_w(17, 'sync_have')
    blk.cmp_wi(9, VALUE_169)
    blk.b_cond('eq', 'sync_area')
    store_flag(blk, 0)                          # image area left 16:9 by another path (dial, i menu, reset):
    blk.b('sync_have')                          # XPan no longer applies
    blk.label('sync_area')
    blk.mov_x64(16, SELECTOR)
    blk.ldr_w(9, 16)
    blk.cbnz_w(9, 'sync_have')                  # video: XPan is a photo setting, keep everything stock
    blk.mov_w(10, 1)
    blk.label('sync_have')
    blk.mov_x64(16, ACTIVE)
    blk.ldrb_w(11, 16)
    blk.mov_x64(14, DEFER)
    blk.cmp_w(11, 10)
    blk.b_cond('ne', 'sync_want')
    blk.strh_w(31, 14)                          # nothing to switch: no wait in progress
    blk.b('sync_done')
    blk.label('sync_want')
    blk.mov_x64(12, PIPE_BUSY)                  # shots still in the pipeline: switch once it drains. The count
    blk.ldr_w(12, 12)                           # is the firmware's own; if it stops changing for DEFER_FRAMES
    blk.cbz_w(12, 'sync_switch')                # presents it is taken as stuck and the switch goes ahead
    blk.mov_x64(17, LAST_BUSY)
    blk.ldr_w(9, 17)
    blk.ldrh_w(13, 14)
    blk.cmp_w(9, 12)
    blk.b_cond('eq', 'sync_same')
    blk.str_w(12, 17)
    blk.strh_w(31, 14)                          # the pipeline is moving: wait on
    blk.b('sync_done')
    blk.label('sync_same')
    blk.cmp_wi(13, DEFER_FRAMES)
    blk.b_cond('hs', 'sync_switch')
    blk.add_xi(13, 13, 1)
    blk.strh_w(13, 14)
    blk.b('sync_done')
    blk.label('sync_switch')                    # to XPan: last row first, so AF rows shrink before extents move;
    blk.strh_w(31, 14)                          # back to stock: first row first, so extents grow before rows do
    blk.mov_w(13, count)
    blk.mov_x64(12, va(SWAP_TABLE_AT))
    blk.mov_w(7, 16)
    blk.cbz_w(10, 'sync_row')
    blk.mov_x64(12, va(SWAP_TABLE_AT) + 16 * (count - 1))
    blk.movn_x(7, 15)                           # x7 = -16
    blk.label('sync_row')
    blk.ldr_x(14, 12, 0)
    blk.ldr_w(9, 12, 8)
    blk.cbz_w(10, 'sync_store')
    blk.ldr_w(9, 12, 12)
    blk.label('sync_store')
    blk.str_w(9, 14, 0)
    blk.add_x(12, 12, 7)
    blk.subs_wi(13, 13, 1)
    blk.b_cond('ne', 'sync_row')
    raw(blk, DMB_ISH, 'dmb ish')
    blk.strb_w(10, 16)                          # recorded only once every word is rewritten
    blk.label('sync_done')
    raw(blk, STLRB, 'stlrb wzr, [x8]')
    blk.ret()
    blk.label('sync_busy')
    raw(blk, CLREX, 'clrex')
    blk.ret()


def af_swaps(img):
    """65:24 AF grids from the stock 16:9 ones: same box, step and edge margins inside the 65:24 band, the most rows
    that fit (same parity, so the middle row stays where it was), centred on the 16:9 grid's middle."""
    out = []
    for base, count in AF_TABLES:
        for mode in range(count):
            at = base + AF_MODE * mode + AF_AREA * VALUE_169
            x0, y0, x1, y1, _, box, _, _, _, step, _, _, rows, pad = struct.unpack_from('<14H', img.old, off(at))
            if rows == 0:
                continue
            slack = y1 - y0 - box - (rows - 1) * step
            require(pad == 0 and step and 0 <= slack < step and BAND_169[0] <= y0 < y1 <= BAND_169[1],
                    f'Unexpected AF grid at {at:#x}')
            room = (BAND_XPAN[1] - (BAND_169[1] - y1)) - (BAND_XPAN[0] + (y0 - BAND_169[0])) - box - slack
            new = max(1, room // step + 1 if room >= 0 else 1)
            if new > 1 and (new - rows) % 2:
                new -= 1
            new = min(new, rows)
            shift = (rows - new) * step                 # rows dropped, half above and half below the middle
            require(shift % 2 == 0, f'AF grid at {at:#x} cannot stay centred')
            top = y0 + shift // 2
            bottom = top + (new - 1) * step + box + slack
            for offset, stock, xpan in ((0, x0 | y0 << 16, x0 | top << 16), (4, x1 | y1 << 16, x1 | bottom << 16),
                                        (24, rows, new)):
                if stock != xpan:
                    out.append((at + offset, stock, xpan))
    return out


def swaps(img, label_id):
    """{RAM address, stock word, XPan word}: still sizes (w | h << 16), then AF grid words."""
    return ([(a, s[0] | s[1] << 16, x[0] | x[1] << 16) for a, s, x in SIZES] + af_swaps(img)
            + [(IMENU_ICON, STOCK_LV, ICON_LV), (IMENU_LABEL, STOCK_LABEL, label_id)])


def swap_table(rows):
    return b''.join(struct.pack('<QII', *row) for row in rows)


def png_grey(raw):
    """Pixels of an 8-bit greyscale, non-interlaced PNG as rows of bytearrays."""
    require(raw[:8] == b'\x89PNG\r\n\x1a\n', 'Not a PNG')
    at, chunks = 8, {}
    while at < len(raw):
        size, kind = struct.unpack_from('>I4s', raw, at)
        chunks[kind] = chunks.get(kind, b'') + raw[at + 8:at + 8 + size]
        at += 12 + size
    width, height, depth, colour, _, _, lace = struct.unpack('>IIBBBBB', chunks[b'IHDR'])
    require((depth, colour, lace) == (8, 0, 0), 'Unexpected icon PNG format')
    data, rows, prev = zlib.decompress(chunks[b'IDAT']), [], bytearray(width)
    for y in range(height):
        kind, line = data[y * (width + 1)], bytearray(data[y * (width + 1) + 1:(y + 1) * (width + 1)])
        for x in range(width):
            a, b, c = line[x - 1] if x else 0, prev[x], prev[x - 1] if x else 0
            p = a + b - c
            pred = (0, a, b, (a + b) >> 1, a if abs(p - a) <= min(abs(p - b), abs(p - c)) else
                    b if abs(p - b) <= abs(p - c) else c)[kind]
            line[x] = line[x] + pred & 0xFF
        rows.append(line)
        prev = line
    return rows


def png_encode(rows):
    """8-bit greyscale PNG with stored (uncompressed) deflate blocks, so the bytes don't depend on the zlib build."""
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    data = b''.join(b'\0' + bytes(r) for r in rows)
    require(len(data) <= 0xFFFF, 'Icon too large for one stored block')
    stream = b'\x78\x01\x01' + struct.pack('<HH', len(data), len(data) ^ 0xFFFF) + data + struct.pack('>I', zlib.adler32(data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', len(rows[0]), len(rows), 8, 0, 0, 0, 0))
            + chunk(b'IDAT', stream) + chunk(b'IEND', b''))


def xpan_icon(img, stock):
    """The stock 16:9 image (36x32 or 36x36) with its text replaced by XPAN; the 28-row frame is centred and its
    inside spans rows top+5..top+22."""
    flags, size, zero, at = struct.unpack_from('<IIQQ', img.old, off(IMAGE_TABLE + 24 * stock))
    require(flags == IMAGE_FLAGS and zero == 0, f'Unexpected image record {stock:#x}')
    rows = png_grey(img.old[off(at):off(at) + size])
    require(len(rows[0]) == 36 and len(rows) in (32, 36), f'Unexpected 16:9 image {stock:#x}')
    top = (len(rows) - 28) // 2
    for y in range(top + 5, top + 23):
        require(rows[y][:2] == rows[y][34:] == b'\xff\xff', f'Unexpected 16:9 image frame {stock:#x}')
        rows[y][2:34] = bytes(32)
    for y, line in enumerate(XPAN_TEXT):
        rows[top + 7 + y][3:33] = bytes({'#': 255, '+': 160, ':': 80, '.': 0}[c] for c in line)
    return png_encode(rows)


def xpan_on(blk, rd):
    """wRD = XPan flag | Fn+dial pending byte (x16 scratch)."""
    load_flag(blk, rd)
    blk.mov_x64(16, PENDING)
    blk.ldrb_w(16, 16)
    blk.orr_w(rd, rd, 16)


def dial(blk, label_id):
    """Fn + command dial: the XPan row of the order table copy follows 16:9 (photo only)."""
    blk.label('dial_flags')                     # 40D3A7BC: 40D4702C filled the allowed bytes; x19 = cursor
    blk.mov_x64(16, DIAL_CURSOR_PHOTO)
    blk.cmp_x(19, 16)
    blk.mov_x64(16, DIAL_TABLE_RAM)
    blk.mov_w(17, 0)
    blk.b_cond('ne', 'dial_flags_set')          # video: never XPan
    blk.ldrb_w(17, 16, 16 * DIAL_ROW_169 + 12)
    blk.label('dial_flags_set')
    blk.strb_w(17, 16, 16 * DIAL_ROW_XPAN + 12)
    raw(blk, DIAL_FLAGS_WORD, 'ldp x19, x30, [sp, #0xc0]')
    blk.b(DIAL_FLAGS_NEXT)
    blk.label('dial_cur')                       # 40D497D4: w24 = current row from [x25]; follow the flag
    raw(blk, DIAL_CUR_WORD, 'ldr w24, [x25]')
    blk.mov_x64(16, DIAL_CURSOR_PHOTO)
    blk.cmp_x(25, 16)
    blk.b_cond('ne', 'dial_cur_done')
    xpan_on(blk, 17)
    blk.cbz_w(17, 'dial_cur_off')
    blk.cmp_wi(24, DIAL_ROW_169)
    blk.b_cond('ne', 'dial_cur_done')
    blk.mov_w(24, DIAL_ROW_XPAN)
    blk.b('dial_cur_done')
    blk.label('dial_cur_off')
    blk.cmp_wi(24, DIAL_ROW_XPAN)
    blk.b_cond('ne', 'dial_cur_done')
    blk.mov_w(24, DIAL_ROW_169)
    blk.label('dial_cur_done')
    blk.b(DIAL_CUR_NEXT)
    blk.label('dial_set')                       # 40D4A384: w9 = value to write for the photo image area
    raw(blk, DIAL_SET_WORD, 'ldr w9, [x8]')
    blk.mov_x64(16, PENDING)
    blk.cmp_wi(9, DIAL_XPAN)
    blk.b_cond('ne', 'dial_set_other')
    blk.mov_w(9, VALUE_169)
    blk.mov_w(17, PENDING_FRAMES)
    blk.strb_w(17, 16)
    blk.b(DIAL_SET_NEXT)
    blk.label('dial_set_other')                 # any other choice: no XPan
    blk.strb_w(31, 16)
    store_flag(blk, 0)
    blk.b(DIAL_SET_NEXT)
    blk.label('fn_popup')                       # 4049AC44: 16:9 slot of the Fn+dial list (w12 icon, w14 label)
    raw(blk, FN_POPUP_WORD, 'mov w14, #0x582')
    xpan_on(blk, 17)
    blk.cbz_w(17, 'fn_popup_done')
    blk.mov_w(12, ICON_LV)
    blk.mov_w(14, label_id)
    blk.label('fn_popup_done')
    blk.b(FN_POPUP_NEXT)


def imenu(blk, label_id):
    """i menu image-area item: dial step with XPan after 16:9, list apply/rollback keep the flag consistent."""
    blk.label('imenu_step')                     # (w0 item, w1 event, w2, w3, x4 -> moved delta) -> w0 changed
    blk.stp_pre(29, 30, -64)
    blk.stp(0, 1, 16)
    blk.stp(2, 3, 32)
    blk.stp(4, 19, 48)
    blk.mov_x64(16, TRANSACTION)                # inside an open DS transaction, remember the flag for a cancel
    blk.ldrb_w(17, 16)                          # (once per transaction; outside one there is nothing to restore)
    blk.cmp_wi(17, 1)
    blk.b_cond('ne', 'imenu_step_disarm')
    blk.mov_x64(16, SAVED)
    blk.ldrb_w(17, 16)
    blk.tbz_w(17, 7, 'imenu_step_arm')
    blk.b('imenu_step_armed')
    blk.label('imenu_step_arm')
    load_flag(blk, 17)
    blk.orr_w_bit(17, 17, 7)                    # armed
    blk.mov_x64(16, SAVED)
    blk.strb_w(17, 16)
    blk.b('imenu_step_armed')
    blk.label('imenu_step_disarm')
    blk.mov_x64(16, SAVED)
    blk.strb_w(31, 16)
    blk.label('imenu_step_armed')
    blk.mov_w(19, 1)                            # w19 = direction: -1 for events 46h/49h, as 404EA570
    blk.cmp_wi(1, 0x46)
    blk.b_cond('eq', 'imenu_step_back')
    blk.cmp_wi(1, 0x49)
    blk.b_cond('ne', 'imenu_step_dir')
    blk.label('imenu_step_back')
    blk.movn_w(19, 0)
    blk.label('imenu_step_dir')
    blk.mov_w32(0, ITEM_ID)
    blk.bl(F_GET)
    blk.cmp_wi(0, VALUE_169)
    blk.b_cond('ne', 'imenu_step_land')
    load_flag(blk, 17)
    blk.cmp_wi(19, 1)
    blk.b_cond('ne', 'imenu_step_from_back')
    blk.cbnz_w(17, 'imenu_step_leave')          # XPan -> next row: clear, stock step
    blk.mov_w(17, 1)                            # 16:9 -> XPan (x16 still holds FLAG)
    blk.strb_w(17, 16)
    blk.b('imenu_step_toggled')
    blk.label('imenu_step_from_back')
    blk.cbz_w(17, 'imenu_step_stock')           # 16:9 -> previous row: stock
    blk.bl('clear_xpan')                        # XPan -> 16:9
    blk.label('imenu_step_toggled')
    blk.ldr_x(4, 31, 48)
    blk.str_w(31, 4)                            # same row: the pop-up cursor doesn't move
    blk.bl('sync')
    blk.mov_w(0, 1)
    blk.b('imenu_step_exit')
    blk.label('imenu_step_leave')
    blk.bl('clear_xpan')
    blk.label('imenu_step_stock')
    blk.ldp(0, 1, 16)
    blk.ldp(2, 3, 32)
    blk.ldr_x(4, 31, 48)
    blk.bl(IMENU_STEP)
    blk.b('imenu_step_exit')
    blk.label('imenu_step_land')                # from another row: landing on 16:9 backwards means XPan
    blk.ldp(0, 1, 16)
    blk.ldp(2, 3, 32)
    blk.ldr_x(4, 31, 48)
    blk.bl(IMENU_STEP)
    blk.str_w(0, 31, 16)
    blk.cmp_wi(19, 1)
    blk.b_cond('eq', 'imenu_step_result')
    blk.mov_w32(0, ITEM_ID)
    blk.bl(F_GET)
    blk.cmp_wi(0, VALUE_169)
    blk.b_cond('ne', 'imenu_step_result')
    store_flag(blk, 1)
    blk.bl('sync')
    blk.label('imenu_step_result')
    blk.ldr_w(0, 31, 16)
    blk.label('imenu_step_exit')
    blk.ldp(4, 19, 48)
    blk.ldp_post(29, 30, 64)
    blk.ret()
    blk.label('imenu_open')                     # list opens: remember the flag (bit 7 = armed) for a cancel
    load_flag(blk, 17)
    blk.orr_w_bit(17, 17, 7)
    blk.mov_x64(16, SAVED)
    blk.strb_w(17, 16)
    blk.b(IMENU_OPEN)
    blk.label('imenu_commit')                   # list confirmed: nothing to restore
    blk.mov_x64(16, SAVED)
    blk.strb_w(31, 16)
    blk.b(IMENU_COMMIT)
    blk.label('imenu_rollback')                 # also called on plain i-menu exits: restore only when the list
    blk.stp_pre(19, 30, -16)                    # was opened and its transaction (A032BBD5C) is still open
    blk.mov_x64(16, TRANSACTION)
    blk.ldrb_w(19, 16)
    blk.mov_x64(16, SAVED)
    blk.ldrb_w(17, 16)
    blk.cmp_wi(19, 1)
    blk.csel_w(19, 17, 31, 'eq')
    blk.strb_w(31, 16)
    blk.bl(IMENU_ROLLBACK)
    blk.tbz_w(19, 7, 'imenu_rollback_done')
    blk.and_w_mask(17, 19, 1)
    blk.mov_x64(16, FLAG)
    blk.strb_w(17, 16)
    blk.label('imenu_rollback_done')
    blk.ldp_post(19, 30, 16)
    blk.ret()
    blk.label('imenu_apply')                    # 404D948C: the list has no XPan row; any image-area apply clears it
    blk.mov_w32(16, ITEM_ID)
    blk.cmp_w(0, 16)
    blk.b_cond('ne', 'imenu_apply_go')
    blk.cmp_wi(1, VALUE_169)                    # the 16:9 row reads XPan while XPan is current: keep it
    blk.b_cond('eq', 'imenu_apply_go')
    blk.mov_x(17, 30)
    blk.bl('clear_xpan')
    blk.mov_x(30, 17)
    blk.label('imenu_apply_go')
    blk.b(F_APPLY)
    blk.label('imenu_popup')                    # 404EA144: dial list slot from triple w8 (3 = 16:9), w12/w10
    blk.cmp_wi(8, 3)
    blk.b_cond('ne', 'imenu_popup_done')
    load_flag(blk, 17)
    blk.cbz_w(17, 'imenu_popup_done')
    blk.mov_w(12, ICON_LV)
    blk.mov_w(10, label_id)
    blk.label('imenu_popup_done')
    raw(blk, IMENU_POPUP_WORD, 'strh w12, [x11, x9, lsl #1]')
    blk.b(IMENU_POPUP_NEXT)


def nef(blk):
    """CropArea of files written while XPan is active: the 65:24 window centred in the 16:9 one."""
    blk.label('croparea')                       # 406E9BFC: x22 = (left, top, width w9, height w8) u16s
    raw(blk, CROP_WORD, 'strh w8, [x22, #6]')
    blk.mov_x64(16, ACTIVE)
    blk.ldrb_w(17, 16)
    blk.cbz_w(17, 'croparea_done')
    raw(blk, LDUR_AREA, 'ldur w10, [x21, #-0x14]')
    blk.cmp_wi(10, VALUE_169)
    blk.b_cond('ne', 'croparea_done')
    blk.mov_w(16, CROP_169[0])
    blk.cmp_w(9, 16)
    blk.b_cond('ne', 'croparea_done')
    blk.mov_w(16, CROP_169[1])
    blk.cmp_w(8, 16)
    blk.b_cond('ne', 'croparea_done')
    blk.ldrh_w(16, 22, 2)
    blk.mov_w(17, (CROP_169[1] - CROP_XPAN_H) // 2)
    blk.add_w(16, 16, 17)
    blk.strh_w(16, 22, 2)
    blk.mov_w(16, CROP_XPAN_H)
    blk.strh_w(16, 22, 6)
    blk.label('croparea_done')
    blk.b(CROP_NEXT)


def icons(blk):
    """XPan icon: image ids past the stock table come from ICON_AT; the 16:9 icon becomes XPan with the flag set."""
    blk.label('img_lookup')                     # 4032DCA0: w8 = image id, x10 = 24, x9 -> table base
    raw(blk, LOOKUP_WORD, 'ldr x9, [x9]')
    blk.mov_w(16, IMAGE_COUNT)
    blk.sub_w(16, 8, 16)
    blk.cmp_wi(16, 2)
    blk.b_cond('hs', 'img_lookup_done')
    blk.mov_x64(9, va(ICON_AT) - 24 * IMAGE_COUNT)
    blk.label('img_lookup_done')
    blk.b(LOOKUP_NEXT)
    blk.label('lv_icon')                        # 4041A9E4: w3 = live view image-area icon; frameless, keep x30
    blk.cmp_wi(3, STOCK_LV)
    blk.b_cond('ne', 'lv_icon_done')
    load_flag(blk, 16)
    blk.cbz_w(16, 'lv_icon_done')
    blk.mov_w(3, ICON_LV)
    blk.label('lv_icon_done')
    raw(blk, LV_ICON_WORD, 'mov w0, #0x25')
    blk.b(LV_ICON_NEXT)
    for n, (_, nxt, rd, _) in enumerate(MENU_ICON_SITES):
        blk.label(f'menu_icon{n}')              # image area is 16:9 here
        blk.mov_w(rd, STOCK_MENU)
        load_flag(blk, 16)
        blk.cbz_w(16, f'menu_icon{n}_done')
        blk.mov_w(rd, ICON_MENU)
        blk.label(f'menu_icon{n}_done')
        blk.b(nxt)


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
    require([img.u32(off(COPY_SITE + 4 * i)) for i in range(4)] == [COPY_WORD, 0xB9400109, 0xB9000149, 0xB9400108]
            and COPY_NEXT == COPY_SITE + 12, 'Unexpected slot copy code')   # ldp; ldr w9,[x8]; str w9,[x10]; ldr w8,[x8]
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
    require(entry(2) == (10, 4, FLAG - 4) and entry(3) == (9, 4, AREA), 'Unexpected photo-bank DS layout')
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
    strip = lambda recs: [(e, 0 if e in OK_EVENTS else a, g, t) for e, a, g, t in recs]
    require(all(strip(t) == strip(out[4]) for t in out), 'Row transitions differ beyond OK')
    out.append([(e, blk.labels['ok_xpan'] if e in OK_EVENTS else a, g, t) for e, a, g, t in out[4]])
    return [b''.join(struct.pack('<4Q', *r) for r in t) for t in out]


def build_main(data, strings):
    """Return Image edits and review metadata; input is immutable MAIN without CRC."""
    img = Image(data)
    page, rows = check_stock(img)
    label_id, = strings.add('xpan', label(img))
    rows_swap = swaps(img, label_id)
    blk = code(len(rows_swap), label_id)
    routine = blk.bytes()
    require(rows[4][5] == STOCK_MENU, 'Unexpected 16:9 row icon')
    row = struct.pack('<11I', label_id, *rows[4][1:5], ICON_MENU, ITEM_XPAN, *rows[4][7:])
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
    page_at, gui_end = at, at + 80
    new_page = bytearray(page)
    struct.pack_into('<2Q', new_page, 16, va(rows_at), va(ptrs_at))
    struct.pack_into('<H', new_page, 32, 6)
    for address, stock, _ in rows_swap:
        require(img.u32(off(address)) == stock, f'Unexpected stock table word at {address:#x}')
    last_flags, last_size, _, last_png = struct.unpack_from('<IIQQ', img.old, off(IMAGE_TABLE + 24 * (IMAGE_COUNT - 1)))
    require(img.old[off(IMAGE_BASE_SITE):off(IMAGE_BASE_SITE) + 16] == mov_x0(IMAGE_TABLE)
            and last_flags == IMAGE_FLAGS and IMAGE_TABLE - 8 < last_png + last_size <= IMAGE_TABLE,
            'Unexpected image table')                # the last record's PNG ends right before the table
    require(all(w % 64 == 0 and h % 2 == 0 for w, h in PANELS + HDMI_SIZES), 'Painter needs w % 64, even h')
    pngs = [xpan_icon(img, STOCK_LV), xpan_icon(img, STOCK_MENU)]
    records, at = b'', ICON_AT + 48
    for png in pngs:
        records += struct.pack('<IIQQ', IMAGE_FLAGS, len(png), 0, va(at))
        at += len(png)
    icon_bytes = records + b''.join(pngs)
    dial_rows = [img.old[off(DIAL_TABLE) + 16 * i:off(DIAL_TABLE) + 16 * i + 16] for i in range(len(DIAL_VALUES))]
    require([struct.unpack_from('<IIII', r) for r in dial_rows] == [(v, 0, 0x7FFFFFFF, 0) for v in DIAL_VALUES],
            'Unexpected Fn+dial image-area table')
    table = (b''.join(dial_rows[:DIAL_ROW_XPAN]) + struct.pack('<IIII', DIAL_XPAN, 0, 0x7FFFFFFF, 0)
             + b''.join(dial_rows[DIAL_ROW_XPAN:]))
    # the free gap CODE_AT..AREA_END holds these in order; each must end before the next starts
    areas = ((CODE_AT, len(routine), 'code'), (GUI_AT, gui_end - GUI_AT, 'GUI records'),
             (SWAP_TABLE_AT, 16 * len(rows_swap), 'swap table'), (ICON_AT, len(icon_bytes), 'icons'),
             (DIAL_TABLE_AT, len(table), 'dial table'), (STATE_AT, STATE_SIZE, 'state'), (AREA_END, 0, 'end'))
    for (start, size, what), (nxt, _, _) in zip(areas, areas[1:]):
        require(start + size <= nxt, f'XPan {what} area too large: ends at {start + size:#x}, {nxt:#x} follows')
    img.put(CODE_AT, routine, 'routine: menu row callbacks, flag bank copy, icons, CropArea, Fn+dial, i menu, bars, sync', expect_zero=True)
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
    img.put(SWAP_TABLE_AT, swap_table(rows_swap), 'still-size and AF-grid words: 16:9 and 65:24 values', expect_zero=True)
    img.put(STATE_AT, bytes(STATE_SIZE), 'XPan runtime state: active, pending, saved, lock, defer, per-unit live view, clean-up and overlay size (zero at load)',
            expect_zero=True)
    img.put(off(START_SITE), branch(START_SITE, blk.labels['lv_start']),
            'display direct input start: record live view running', expect=struct.pack('<I', START_WORD))
    img.put(off(STOP_SITE), branch(STOP_SITE, blk.labels['lv_stop']),
            'display direct input stop: record live view stopped', expect=struct.pack('<I', START_WORD))
    img.put(off(OSIZE_SITE), branch(OSIZE_SITE, blk.labels['osize']),
            'overlay layer size store: record overlay width and height', expect=struct.pack('<I', OSIZE_WORD))
    img.put(off(PRESENT_SITE), branch(PRESENT_SITE, blk.labels['present'], link=True),
            'overlay bank request: paint 65:24 bars first', expect=branch(PRESENT_SITE, F_PRESENT, link=True))
    img.put(off(CROP_SITE), branch(CROP_SITE, blk.labels['croparea']),
            'MakerNote CropArea: 65:24 window while XPan is active', expect=struct.pack('<I', CROP_WORD))
    img.put(off(HDMI_SITE), branch(HDMI_SITE, blk.labels['hdmi_present'], link=True),
            'HDMI overlay bank request: sync, then 65:24 bars', expect=branch(HDMI_SITE, F_PRESENT, link=True))
    img.put(ICON_AT, icon_bytes, 'image records 1862h/1863h and their PNGs: XPan icons', expect_zero=True)
    img.put(off(LOOKUP_SITE), branch(LOOKUP_SITE, blk.labels['img_lookup']),
            'image lookup: ids 1862h/1863h from the XPan records', expect=struct.pack('<I', LOOKUP_WORD))
    img.put(off(LV_ICON_SITE), branch(LV_ICON_SITE, blk.labels['lv_icon']),
            'live view image-area icon: XPan instead of 16:9 with the flag set', expect=struct.pack('<I', LV_ICON_WORD))
    for n, (site, _, _, word) in enumerate(MENU_ICON_SITES):
        img.put(off(site), branch(site, blk.labels[f'menu_icon{n}']),
                'Image area row icon: XPan instead of 16:9 with the flag set', expect=struct.pack('<I', word))
    img.put(DIAL_TABLE_AT, table, 'Fn+dial image-area order table: stock rows plus XPan after 16:9', expect_zero=True)
    count = struct.pack('<I', 0x52800008 | (len(DIAL_VALUES) + 1) << 5)
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    require([(i.mnemonic, i.op_str) for i in md.disasm(count + mov_x0(DIAL_TABLE_RAM), DIAL_COUNT_SITE)] ==
            [('mov', 'w8, #8'), ('mov', f'x0, #{DIAL_TABLE_RAM & 0xFFFF:#x}'),
             ('movk', f'x0, #{DIAL_TABLE_RAM >> 16 & 0xFFFF:#x}, lsl #16'),
             ('movk', f'x0, #{DIAL_TABLE_RAM >> 32 & 0xFFFF:#x}, lsl #32'),
             ('movk', 'x0, #0, lsl #48')], 'Bad Fn+dial table count/address encoding')
    img.put(off(DIAL_COUNT_SITE), count, 'Fn+dial image-area table: eight rows', expect=struct.pack('<I', DIAL_COUNT_WORD))
    img.put(off(DIAL_ADDR_SITE), mov_x0(DIAL_TABLE_RAM), 'Fn+dial image-area table: writable copy (text RAM alias)',
            expect=mov_x0(DIAL_TABLE))
    for site, name, word, why in ((DIAL_FLAGS_SITE, 'dial_flags', DIAL_FLAGS_WORD, 'Fn+dial: XPan row allowed like 16:9'),
                                  (DIAL_CUR_SITE, 'dial_cur', DIAL_CUR_WORD, 'Fn+dial: current row follows the flag'),
                                  (DIAL_SET_SITE, 'dial_set', DIAL_SET_WORD, 'Fn+dial: XPan writes 16:9, then the flag'),
                                  (FN_POPUP_SITE, 'fn_popup', FN_POPUP_WORD, 'Fn+dial list: XPan icon and label'),
                                  (IMENU_POPUP_SITE, 'imenu_popup', IMENU_POPUP_WORD, 'i-menu dial list: XPan icon and label')):
        img.put(off(site), branch(site, blk.labels[name]), why, expect=struct.pack('<I', word))
    img.put(off(IMENU_APPLY_SITE), branch(IMENU_APPLY_SITE, blk.labels['imenu_apply']),
            'i-menu list apply: image area clears XPan', expect=branch(IMENU_APPLY_SITE, F_APPLY))
    for slot, name, stock in ((0x10, 'imenu_step', IMENU_STEP), (0x20, 'imenu_open', IMENU_OPEN),
                              (0x28, 'imenu_commit', IMENU_COMMIT), (0x30, 'imenu_rollback', IMENU_ROLLBACK)):
        img.put(off(IMENU_DESC + slot), struct.pack('<Q', blk.labels[name]), f'i-menu image area: {name}',
                expect=struct.pack('<Q', stock))
    listing = blk.listing(md)                   # every hand-encoded word must read back as the text it was given
    for x, w, t in listing:
        word = int.from_bytes(bytes.fromhex(w), 'little')
        require(word not in RAWS or t == RAWS[word], f'Bad raw encoding at {x:#x}: {t}')
    return img, dict(prototype=True, label_id=label_id, flag=hex(FLAG), item=hex(ITEM_XPAN),
                     routine=hex(va(CODE_AT)), page=hex(va(page_at)), labels={k: hex(v) for k, v in blk.labels.items()},
                     code=[dict(va=hex(x), word=w, text=t) for x, w, t in listing])
