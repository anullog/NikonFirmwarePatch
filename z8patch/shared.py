"""Own the shared strings, menu-bank seam and text-copy boundary exactly once."""
import struct
from z8prores.package import require
from z8prores.a64 import Block
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

BASE = 0x40160000
STRING_IDS = {'opengate-nraw': (3137, 5), 'pixelshift': (3142, 5), 'prores': (3147, 6), 'xpan': (3153, 1)}
TEXT = 0x408D0000
TABLES = 0x408E0000
BANK = 0x40980000


class Strings:
    def __init__(self):
        self.groups = {}

    def add(self, name, translations):
        first, count = STRING_IDS[name]
        require(name not in self.groups, 'Duplicate string provider')
        require(len(translations) == 36 and all(len(t) == count for t in translations), 'String provider shape')
        self.groups[name] = translations
        return list(range(first, first+count))

    def emit(self, img):
        if not self.groups:
            return {}
        text, tables = bytearray(), bytearray()
        # 3137..3152 for the reviewed features; later ids extend the table only when selected
        last = max([3153] + [sum(STRING_IDS[name]) for name in self.groups])
        for language in range(36):
            table = img.u64(0x311C5A0+8*language)-BASE
            blob = img.u64(0x387AF58+8*language)-BASE
            offsets = list(struct.unpack_from('<3137i', img.old, table))
            for ident in range(3137, last):
                value = ''
                for name, translations in self.groups.items():
                    first, count = STRING_IDS[name]
                    if first <= ident < first+count:
                        value = translations[language][ident-first]
                offsets.append(TEXT+len(text)-BASE-blob)
                text.extend(value.encode('utf-16le'))
            offsets.append(TEXT+len(text)-BASE-blob)
            require(len(offsets) == last+1, 'Shared string table shape')
            at = TABLES+len(tables)
            tables.extend(struct.pack(f'<{last+1}i', *offsets))
            img.put(0x311C5A0+8*language, struct.pack('<Q', at), f'shared language directory {language}')
        require(TEXT+len(text) <= TABLES and TABLES+len(tables) <= 0x40950000, 'Shared strings overflow')
        img.put(TEXT-BASE, text, 'shared selected-feature strings', expect_zero=True)
        img.put(TABLES-BASE, tables, 'shared 36 language offset tables', expect_zero=True)
        return dict(features=sorted(self.groups), entries=last+1, text_bytes=len(text), table_bytes=len(tables))


def bank(img, features):
    pro, og = 'prores' in features, 'opengate-nraw' in features
    if not (pro or og):
        return None
    b = Block(BANK)
    b.ldp(10, 8, 0)
    b.lsl_wi(9, 20, 16)
    b.mov_w(11, 0)
    b.movk_w16(11, 0x120B)
    b.cmp_w(9, 11)
    b.b_cond('ne', 'native')
    # Copy only selected settings; +2 is a playback cache, never a bank setting.
    for offset in ([1] if pro else []) + ([3] if og else []):
        b.ldrb_w(9, 8, offset)
        b.strb_w(9, 10, offset)
    b.label('native')
    b.b(0x410162F0)
    b.listing(Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN))
    img.put(BANK-BASE, b.bytes(), 'shared menu-bank setting copy', expect_zero=True)
    delta = BANK-0x410162EC
    img.put(0xEB62EC, struct.pack('<I', 0x14000000 | ((delta//4)&0x3FFFFFF)),
            'shared bank hook', expect=bytes.fromhex('ea2340a9'))
    return dict(va=hex(BANK), copied_extra_bytes=([1] if pro else [])+([3] if og else []), playback_byte_preserved=2)


def loader(img, features):
    floor = 0x408BF8F0
    if 'opengate-nraw' in features:
        floor = max(floor, 0x40970000)  # includes zero-initialized OG_STATE and Q3 stash
    if 'pixelshift' in features:
        floor = max(floor, 0x409F0000)  # includes the cold-start private runtime page
    used = max([floor]+[BASE+e['end'] for e in img.edits if 0x75F8F0 <= e['offset'] < 0xAA0000])
    end = (used+4095)&~4095
    require(end <= 0x40C00000, 'Text loader exceeds its original mapped section')
    data = struct.pack('<II', 0xD2800008 | ((end&65535)<<5), 0xF2A00008 | ((end>>16)<<5))
    img.put(0x1024D8C, data, 'shared text copy boundary', expect=bytes.fromhex('081e9fd26811a8f2'))
    return dict(end=hex(end), includes_zero_state=True)
