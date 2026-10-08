# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""QR codes (ISO/IEC 18004), for the sharing address a phone's camera reads: byte mode,
versions 1 to 10, error correction L, M (the default), Q or H, the mask chosen by the
standard's penalty rules.

    code = qr.encode('http://192.168.1.20:8095/')   # QrCode; ValueError when too long
    code.size                    # modules per side (21 for version 1, 4 more per version)
    code.version, code.ecc, code.mask
    code.modules[y][x]           # True for a dark module
    code.dark(x, y)
    code.text(border=4)          # the code in two characters per module ('██', '  ')

The quiet zone (four light modules around the code) is not part of `modules`; whoever draws
the code leaves it. No GTK: widgets/qr_code.py draws one.
"""

ECC_LEVELS = ('L', 'M', 'Q', 'H')
FORMAT_BITS = {'L': 1, 'M': 0, 'Q': 3, 'H': 2}
MAX_VERSION = 10

# Per version (index 1 to 10): error correction codewords per block, and number of blocks.
ECC_PER_BLOCK = {
    'L': (None, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18),
    'M': (None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),
    'Q': (None, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24),
    'H': (None, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28),
}
BLOCKS = {
    'L': (None, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4),
    'M': (None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),
    'Q': (None, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8),
    'H': (None, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8),
}

PENALTY_RUN = 3
PENALTY_BOX = 3
PENALTY_FINDER = 40
PENALTY_BALANCE = 10
FINDER_LIKE = ((True, False, True, True, True, False, True, False, False, False, False),
               (False, False, False, False, True, False, True, True, True, False, True))

MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


# -- Reed-Solomon over GF(256), the polynomial x^8 + x^4 + x^3 + x^2 + 1 ----------------------

_EXP = [0] * 512
_LOG = [0] * 256
_value = 1
for _power in range(255):
    _EXP[_power] = _value
    _LOG[_value] = _power
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _power in range(255, 512):
    _EXP[_power] = _EXP[_power - 255]


def _multiply(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    """The generator polynomial's coefficients, highest power first (its leading 1 left
    out): the product of (x - α^i) for i below degree."""
    poly = [1]
    for i in range(degree):
        poly = [a ^ _multiply(b, _EXP[i]) for a, b in zip(poly + [0], [0] + poly, strict=True)]
    return poly[1:]


def reed_solomon(data, degree):
    """The `degree` error correction codewords of `data` (bytes or a list of ints)."""
    generator = _generator(degree)
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for i, coefficient in enumerate(generator):
            remainder[i] ^= _multiply(coefficient, factor)
    return remainder


# -- capacity ----------------------------------------------------------------------------------

def size_of(version):
    return version * 4 + 17


def alignment_positions(version):
    """The rows (and columns) alignment patterns are centred on."""
    if version == 1:
        return []
    count = version // 7 + 2
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    last = size_of(version) - 7
    return [6] + sorted(last - i * step for i in range(count - 1))


def raw_modules(version):
    """The modules left for data and error correction, function patterns taken away."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        result -= (25 * count - 10) * count - 55
        if version >= 7:
            result -= 36
    return result


def codewords(version):
    return raw_modules(version) // 8


def data_codewords(version, ecc):
    return codewords(version) - ECC_PER_BLOCK[ecc][version] * BLOCKS[ecc][version]


def capacity(version, ecc='M'):
    """How many bytes fit in byte mode."""
    count_bits = 8 if version < 10 else 16
    return (data_codewords(version, ecc) * 8 - 4 - count_bits) // 8


# -- encoding ----------------------------------------------------------------------------------

class QrCode:
    """A QR code's modules; see the module docstring."""

    def __init__(self, version, ecc, mask, modules):
        self.version = version
        self.ecc = ecc
        self.mask = mask
        self.modules = modules
        self.size = len(modules)

    def dark(self, x, y):
        return self.modules[y][x]

    def text(self, border=4):
        light = '  ' * (self.size + 2 * border)
        rows = [light] * border
        for row in self.modules:
            rows.append('  ' * border + ''.join('██' if dark else '  ' for dark in row)
                        + '  ' * border)
        rows += [light] * border
        return '\n'.join(rows)


def encode(text, ecc='M', version=None, mask=None):
    """The QR code of `text` (a str, encoded as UTF-8, or bytes) in byte mode: the smallest
    version that holds it unless `version` says, the best mask unless `mask` says. Raises
    ValueError when it does not fit in version 10."""
    if ecc not in ECC_LEVELS:
        raise ValueError(f'Unknown error correction level {ecc!r}')
    data = text.encode('utf-8') if isinstance(text, str) else bytes(text)
    versions = [version] if version is not None else range(1, MAX_VERSION + 1)
    for candidate in versions:
        if not 1 <= candidate <= MAX_VERSION:
            raise ValueError(f'Version {candidate} is not supported')
        if len(data) <= capacity(candidate, ecc):
            version = candidate
            break
    else:
        raise ValueError(f'{len(data)} bytes do not fit in a QR code of version '
                         f'{versions[-1]}')
    stream = _codewords(_data_bits(data, version, ecc), version, ecc)
    matrix = _Matrix(version)
    matrix.draw_function_patterns()
    matrix.place(stream)
    if mask is None:
        best = None
        for candidate in range(8):
            score = matrix.masked(candidate, ecc).penalty()
            if best is None or score < best[0]:
                best = (score, candidate)
        mask = best[1]
    result = matrix.masked(mask, ecc)
    return QrCode(version, ecc, mask, [list(row) for row in result.modules])


def _data_bits(data, version, ecc):
    bits = []

    def append(value, length):
        bits.extend((value >> shift) & 1 for shift in range(length - 1, -1, -1))

    append(0b0100, 4)  # byte mode
    append(len(data), 8 if version < 10 else 16)
    for byte in data:
        append(byte, 8)
    capacity_bits = data_codewords(version, ecc) * 8
    append(0, min(4, capacity_bits - len(bits)))  # the terminator
    append(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity_bits:
        append(pad, 8)
        pad ^= 0xEC ^ 0x11
    return [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]


def _codewords(data, version, ecc):
    """The data split into blocks, each with its error correction, interleaved."""
    blocks_count = BLOCKS[ecc][version]
    ecc_length = ECC_PER_BLOCK[ecc][version]
    total = codewords(version)
    short_blocks = blocks_count - total % blocks_count
    short_length = total // blocks_count - ecc_length
    blocks, start = [], 0
    for index in range(blocks_count):
        length = short_length + (0 if index < short_blocks else 1)
        part = data[start:start + length]
        start += length
        blocks.append((part, reed_solomon(part, ecc_length)))
    result = []
    for i in range(short_length + 1):
        for part, _ecc in blocks:
            if i < len(part):
                result.append(part[i])
    for i in range(ecc_length):
        for _part, ecc_part in blocks:
            result.append(ecc_part[i])
    return result


class _Matrix:

    def __init__(self, version):
        self.version = version
        self.size = size_of(version)
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def copy(self):
        other = _Matrix.__new__(_Matrix)
        other.version, other.size = self.version, self.size
        other.modules = [list(row) for row in self.modules]
        other.function = self.function
        return other

    def set(self, x, y, dark):
        self.modules[y][x] = dark
        self.function[y][x] = True

    def draw_function_patterns(self):
        size = self.size
        for i in range(size):  # the timing patterns
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for x, y in ((3, 3), (size - 4, 3), (3, size - 4)):
            self._finder(x, y)
        positions = alignment_positions(self.version)
        last = len(positions) - 1
        for i, x in enumerate(positions):
            for j, y in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # a finder is there
                self._alignment(x, y)
        self._format(0, 0)  # reserved; drawn for real once the mask is known
        self._version_info()

    def _finder(self, cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size:
                    distance = max(abs(dx), abs(dy))
                    self.set(x, y, distance not in (2, 4))

    def _alignment(self, cx, cy):
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self.set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def _format(self, ecc_bits, mask):
        data = ecc_bits << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1 == 1

        size = self.size
        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, size - 15 + i, bit(i))
        self.set(8, size - 8, True)  # the dark module

    def _version_info(self):
        if self.version < 7:
            return
        remainder = self.version
        for _ in range(12):
            remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
        bits = self.version << 12 | remainder
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a, b = self.size - 11 + i % 3, i // 3
            self.set(a, b, dark)
            self.set(b, a, dark)

    def place(self, stream):
        """The codewords' bits in the zigzag of two-module columns, right to left."""
        bits = [(byte >> shift) & 1 == 1 for byte in stream for shift in range(7, -1, -1)]
        index = 0
        size = self.size
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5  # the vertical timing pattern
            upward = (right + 1) & 2 == 0
            for vertical in range(size):
                y = size - 1 - vertical if upward else vertical
                for x in (right, right - 1):
                    if not self.function[y][x] and index < len(bits):
                        self.modules[y][x] = bits[index]
                        index += 1
            right -= 2

    def masked(self, mask, ecc):
        result = self.copy()
        test = MASKS[mask]
        for y in range(self.size):
            row, function = result.modules[y], self.function[y]
            for x in range(self.size):
                if not function[x] and test(x, y):
                    row[x] = not row[x]
        result.function = [list(row) for row in self.function]
        result._format(FORMAT_BITS[ecc], mask)
        return result

    def penalty(self):
        """The standard's score of how hard the code is to read (lower is better)."""
        modules = self.modules
        size = self.size
        columns = [[modules[y][x] for y in range(size)] for x in range(size)]
        score = 0
        for line in modules + columns:
            run, previous = 0, None
            for dark in line:
                if dark == previous:
                    run += 1
                else:
                    if run >= 5:
                        score += PENALTY_RUN + run - 5
                    run, previous = 1, dark
            if run >= 5:
                score += PENALTY_RUN + run - 5
            for start in range(size - 10):
                if tuple(line[start:start + 11]) in FINDER_LIKE:
                    score += PENALTY_FINDER
        for y in range(size - 1):
            for x in range(size - 1):
                dark = modules[y][x]
                if dark == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                    score += PENALTY_BOX
        dark = sum(sum(row) for row in modules)
        total = size * size
        score += abs(dark * 100 - total * 50) // (total * 5) * PENALTY_BALANCE
        return score
