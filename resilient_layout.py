"""Layout-independent fallbacks for Chromium compiler changes (read-only analysis).

The strict analyzers in ``runtime_gamma22`` model specific compiler layouts of
the two patch sites.  Every new Chrome/Edge toolchain revision tends to change
those layouts (call-site shapes, loop fusion, initializer counts) even though
the program logic is identical.  This module provides fallbacks that depend on
facts which do not change with code generation:

GPU process (gamma 2.2 interpretation of BT.709/sRGB)
    Every place that builds a color space from the canonical sRGB transfer
    function *immediately followed by* the BT.709 gamut constant is redirected
    to the adjacent gamma 2.2 constant.  The recognized load forms (lea, mov,
    SSE/AVX loads; Edge's lea/lea/call constructors and Chrome's movups pairs
    alike) all keep the 32-bit RIP displacement in their last four bytes, so
    only that displacement changes.  The number of sites, their encoding and
    any factory/singleton shape no longer matter.  Loads of the same constant
    that are not paired with BT.709 (Display-P3 and other wide-gamut spaces,
    Skia's own "is this sRGB" comparisons) are deliberately left unchanged.

Browser process (SDR output on the scRGB/F16 path)
    ``gfx::DisplayColorSpaces::SetOutputColorSpaceAndFormat`` is a tiny leaf
    function whose exact bytes have been stable across every analyzed Chrome
    and Edge build.  ScreenWin calls it only in its HDR branch, for the
    kWideColorGamut and kHDR usages.  A detour at its entry repeats every
    kWideColorGamut call for kSRGB first, which is exactly what the strict
    loop/call-site patches achieve, but without depending on the caller's
    loop shape.

Both fallbacks keep the project's fail-closed rules: unique matches only,
complete original-byte verification and no changes to files on disk.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from gamma22_patcher import (
    GAMMA22_TRANSFER_FUNCTION,
    HDR_OUTPUT_HELPER_BYTES,
    PatchError,
    SRGB_GAMUT,
    SRGB_TRANSFER_FUNCTION,
    find_all_rvas,
    rel32,
    rva_to_offset,
)

IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_WRITE = 0x80000000

# Strict analyzers saw 47-49 (Chrome pairs) and 97-102 (Edge) constructors;
# Electron 45 (Chromium codegen) has 53.  Fewer paired sites means this is not
# the gfx sRGB constructor constant.
MIN_PAIRED_USES = 16
# Unpaired uses are left alone, but many of them means the block is a generic
# Skia constant rather than the color-space constructor constant.
MAX_UNPAIRED_USES = 16
# Window in which the next RIP data load after an sRGB use must be the gamut.
GAMUT_PAIR_WINDOW = 64

DETOUR_MAX_HELPER_REFERENCES = 32

# Labels are role-routed by hot_attach_gamma22.reconcile_module_for_role.
LABEL_GAMMA_PREFIX = "sRGB initializer "
LABEL_DETOUR_STUB = "ScreenWin output detour stub"
LABEL_DETOUR_HOOK = "ScreenWin output detour hook"

# The helper prologue replaced by the 5-byte jump: push rsi; push rdi;
# mov rax, rcx.  These are three complete position-independent instructions.
HELPER_PROLOGUE = HDR_OUTPUT_HELPER_BYTES[:5]
assert HELPER_PROLOGUE == bytes.fromhex("56 57 48 89 C8")


def section_table(path: Path) -> list[tuple[str, int, int, int, int, int]]:
    """Return (name, rva, virtual_size, raw_offset, raw_size, characteristics)."""
    with path.open("rb") as stream:
        dos = stream.read(0x40)
        pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
        stream.seek(pe_offset)
        header = stream.read(24)
        if header[:4] != b"PE\0\0":
            raise PatchError("Not a PE file (missing PE signature)")
        count = struct.unpack_from("<H", header, 6)[0]
        optional_size = struct.unpack_from("<H", header, 20)[0]
        stream.seek(pe_offset + 24 + optional_size)
        result = []
        for _ in range(count):
            entry = stream.read(40)
            name = entry[:8].rstrip(b"\0").decode("ascii", "replace")
            vsize, rva, raw_size, raw_offset = struct.unpack_from("<IIII", entry, 8)
            flags = struct.unpack_from("<I", entry, 36)[0]
            result.append((name, rva, vsize, raw_offset, raw_size, flags))
        return result


def _section_for(table, rva):
    for entry in table:
        _name, start, vsize, _raw_offset, raw_size, _flags = entry
        if start <= rva < start + max(vsize, raw_size):
            return entry
    return None


def _read(path: Path, sections, rva: int, size: int) -> bytes:
    with path.open("rb") as stream:
        stream.seek(rva_to_offset(sections, rva))
        data = stream.read(size)
    if len(data) != size:
        raise PatchError(f"Short read at RVA 0x{rva:X}")
    return data


# RIP-relative loads/address computations that compilers use for constant
# data: lea/mov r64 (REX.W), SSE movups/movaps/movdqu/movdqa/movss/movsd
# (prefixes and REX precede the 0F escape), and 2-/3-byte VEX forms.
_RIP_MODRM = rb"[\x05\x0D\x15\x1D\x25\x2D\x35\x3D]"
_RIP_REFERENCE = re.compile(
    rb"(?=("
    rb"[\x48\x4C\x49\x4D][\x8D\x8B]" + _RIP_MODRM + rb"...."
    rb"|\x0F[\x10\x28\x6F]" + _RIP_MODRM + rb"...."
    rb"|\xC5.[\x10\x28\x6F]" + _RIP_MODRM + rb"...."
    rb"|\xC4..[\x10\x28\x6F]" + _RIP_MODRM + rb"...."
    rb"))",
    re.S,
)


def rip_references(text: bytes, text_rva: int, start: int = 0, stop: int | None = None):
    """Yield (instruction_rva, target_rva, length) for recognized RIP loads."""
    stop = len(text) if stop is None else min(stop, len(text))
    for match in _RIP_REFERENCE.finditer(text, max(0, start), stop):
        instruction = match.group(1)
        position = match.start()
        if position + len(instruction) > stop:
            continue
        end = text_rva + position + len(instruction)
        yield text_rva + position, end + struct.unpack_from("<i", instruction, len(instruction) - 4)[0], len(instruction)


def _text_section(sections, path: Path):
    try:
        _name, text_rva, _vsize, text_offset, text_size = next(
            section for section in sections if section[0] == ".text"
        )
    except StopIteration as error:
        raise PatchError("PE file has no .text section") from error
    with path.open("rb") as stream:
        stream.seek(text_offset)
        return stream.read(text_size), text_rva


def _load_width(text: bytes, text_rva: int, rva: int) -> int | None:
    """Bytes a recognized reference makes available; None = whole object (lea)."""
    head = text[rva - text_rva:rva - text_rva + 3]
    if head[0] in (0x48, 0x4C, 0x49, 0x4D):
        return None if head[1] == 0x8D else 8          # lea / mov r64
    if head[0] == 0xC5:
        return 32 if head[1] & 0x04 else 16            # VEX.L selects ymm
    if head[0] == 0xC4:
        return 32 if text[rva - text_rva + 2] & 0x04 else 16
    return 16                                          # SSE xmm load


def _transfer_uses(text: bytes, text_rva: int, references):
    """Group transfer-function references into complete uses.

    Edge passes the constant's address once (lea).  Chrome copies the 28 bytes
    with two 16-byte loads ([+0] and [+12]) in either order.  A use is
    redirectable only when it covers the whole function; partial copies are
    reported as incomplete (``None``) and never rewritten, so a transfer
    function can never become half sRGB and half gamma 2.2.
    Returns [(references, complete)].
    """
    uses = []
    references = sorted(references)
    index = 0
    while index < len(references):
        cluster = [references[index]]
        index += 1
        while (
            index < len(references)
            and references[index][0] - cluster[0][0] <= 24
            and references[index][1] not in {item[1] for item in cluster}
        ):
            cluster.append(references[index])
            index += 1
        covered = set()
        complete = False
        for rva, offset, _length in cluster:
            width = _load_width(text, text_rva, rva)
            if width is None:
                complete = complete or (offset == 0 and len(cluster) == 1)
            else:
                covered.update(range(offset, offset + width))
        if covered and len(cluster) <= 2:
            complete = set(range(len(SRGB_TRANSFER_FUNCTION))) <= covered
        uses.append((cluster, complete))
    return uses


def _followed_by_gamut(text, text_rva, use, gamut) -> bool:
    """The next RIP data load after one sRGB use must be the BT.709 gamut."""
    cluster, complete = use
    if not complete:
        return False
    last_rva, _offset, last_length = max(cluster)
    start = last_rva + last_length - text_rva
    for _rva, target, _length in rip_references(text, text_rva, start, start + GAMUT_PAIR_WINDOW):
        return target == gamut
    return False


def discover_gamma_pairs_layout(dll: Path, sections, text=None, text_rva=None) -> dict:
    """Find BT.709 sRGB constructor loads that can be redirected to gamma 2.2."""
    if text is None:
        text, text_rva = _text_section(sections, dll)
    table = section_table(dll)
    gamuts = find_all_rvas(dll, sections, SRGB_GAMUT)
    srgbs = find_all_rvas(dll, sections, SRGB_TRANSFER_FUNCTION)
    gammas = find_all_rvas(dll, sections, GAMMA22_TRANSFER_FUNCTION)

    blocks = []
    for srgb in srgbs:
        section = _section_for(table, srgb)
        if section is None or section[5] & (
            IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_CNT_CODE
        ):
            continue  # constants live in immutable data, never code or globals
        near_gamuts = [rva for rva in gamuts if 0 < srgb - rva <= 0x60]
        near_gammas = [rva for rva in gammas if 0 < rva - srgb <= 0x60]
        if len(near_gamuts) == 1 and len(near_gammas) == 1:
            blocks.append((near_gamuts[0], srgb, near_gammas[0]))
    if not blocks:
        raise PatchError("Fallback found no BT.709/sRGB/gamma 2.2 constant block")

    transfer_refs: dict[int, list] = {index: [] for index in range(len(blocks))}
    for rva, target, length in rip_references(text, text_rva):
        for index, (_gamut, srgb, _gamma) in enumerate(blocks):
            if srgb <= target < srgb + len(SRGB_TRANSFER_FUNCTION):
                transfer_refs[index].append((rva, target - srgb, length))

    accepted = []
    reasons = []
    for index, (gamut, srgb, gamma) in enumerate(blocks):
        uses = _transfer_uses(text, text_rva, transfer_refs[index])
        paired = [cluster for cluster, complete in uses
                  if _followed_by_gamut(text, text_rva, (cluster, complete), gamut)]
        unpaired = [cluster[0][0] for cluster, _complete in uses if cluster not in paired]
        if len(paired) < MIN_PAIRED_USES or len(unpaired) > MAX_UNPAIRED_USES:
            reasons.append(
                f"0x{srgb:X}: {len(paired)} BT.709 pair(s), {len(unpaired)} other load(s)"
            )
            continue
        accepted.append(dict(
            srgb_gamut_rva=gamut,
            srgb_transfer_rva=srgb,
            gamma22_transfer_rva=gamma,
            gamma_pair_uses=paired,
            gamma_unpaired_rvas=unpaired,
        ))
    if len(accepted) != 1:
        raise PatchError(
            f"Fallback expected one BT.709 sRGB constructor constant; found {len(accepted)}"
            + (f" ({'; '.join(reasons[:4])})" if reasons else "")
        )
    return accepted[0]


def redirect_load(original: bytes, instruction_rva: int, new_target: int) -> bytes:
    """Replace the trailing RIP displacement of one recognized load."""
    return original[:-4] + rel32(instruction_rva, len(original), new_target)


# One detour instruction: (kind, payload).  "raw" carries finished bytes;
# "jne"/"call" target a label; "jmp" targets an absolute RVA.
_DETOUR_PROGRAM = (
    ("raw", "80 FA 01"),         # cmp dl, 1            ; kWideColorGamut?
    ("jne", "original"),         # jne original         ; every other usage
    ("raw", "51"),               # push rcx
    ("raw", "41 50"),            # push r8
    ("raw", "41 51"),            # push r9
    ("raw", "48 83 EC 30"),      # sub rsp, 30h         ; keeps 16-byte alignment
    ("raw", "48 8B 44 24 70"),   # mov rax, [rsp+70h]   ; caller's 5th argument
    ("raw", "48 89 44 24 20"),   # mov [rsp+20h], rax
    ("raw", "31 D2"),            # xor edx, edx         ; usage = kSRGB
    ("call", "original"),        # call original        ; returns here via its ret
    ("raw", "48 83 C4 30"),      # add rsp, 30h
    ("raw", "41 59"),            # pop r9
    ("raw", "41 58"),            # pop r8
    ("raw", "59"),               # pop rcx
    ("raw", "B2 01"),            # mov dl, 1            ; original usage
    ("label", "original"),
    ("raw", "56 57 48 89 C8"),   # displaced prologue: push rsi; push rdi; mov rax, rcx
    ("resume", None),            # jmp helper+5
)
_LINK_SIZE = 5
_SIZES = {"jne": 6, "call": 5, "resume": 5}


def _instruction_size(kind, payload):
    if kind == "raw":
        return len(bytes.fromhex(payload))
    return _SIZES.get(kind, 0)


def build_output_detour_chunks(caves, helper_rva: int):
    """Assemble the detour stub across one or more code caves.

    ``caves`` is a list of (rva, capacity).  Instructions are never split;
    a chunk that cannot hold the next instruction ends with ``jmp`` to the
    next cave.  Returns [(rva, bytes)] in execution order.

    Entry state: rcx=this, dl=ContentColorUsage, r8b=needs_alpha,
    r9=const ColorSpace*, [rsp+28h]=8-byte buffer format.  For usage 1
    (kWideColorGamut) the original body first runs with usage 0 (kSRGB) and
    identical arguments, then the original call proceeds unchanged.  The
    verified helper only writes its object, preserves rsi/rdi and never uses
    its shadow space.
    """
    placed = []  # (chunk, offset, kind, payload)
    labels = {}
    pending = []
    chunk = 0
    offset = 0
    for kind, payload in _DETOUR_PROGRAM:
        if kind == "label":
            pending.append(payload)
            continue
        size = _instruction_size(kind, payload)
        reserve = 0 if kind == "resume" else _LINK_SIZE
        while offset + size + reserve > caves[chunk][1]:
            placed.append((chunk, offset, "link", None))
            chunk += 1
            offset = 0
            if chunk >= len(caves):
                raise PatchError("Fallback detour caves are too small for the stub")
        for name in pending:
            labels[name] = (chunk, offset)
        pending = []
        placed.append((chunk, offset, kind, payload))
        offset += size

    def address(position):
        chunk_index, chunk_offset = position
        return caves[chunk_index][0] + chunk_offset

    buffers = [bytearray() for _ in range(chunk + 1)]
    for chunk_index, chunk_offset, kind, payload in placed:
        rva = caves[chunk_index][0] + chunk_offset
        buffer = buffers[chunk_index]
        assert len(buffer) == chunk_offset
        if kind == "raw":
            buffer.extend(bytes.fromhex(payload))
        elif kind == "jne":
            buffer.extend(b"\x0F\x85" + rel32(rva, 6, address(labels[payload])))
        elif kind == "call":
            buffer.extend(b"\xE8" + rel32(rva, 5, address(labels[payload])))
        elif kind == "resume":
            buffer.extend(b"\xE9" + rel32(rva, 5, helper_rva + len(HELPER_PROLOGUE)))
        elif kind == "link":
            buffer.extend(b"\xE9" + rel32(rva, 5, caves[chunk_index + 1][0]))
    return [(caves[index][0], bytes(buffer)) for index, buffer in enumerate(buffers)]


def find_padding_caves(dll: Path, sections, text: bytes, text_rva: int, minimum: int = 16):
    """Return (rva, capacity) INT3-only gaps between declared functions.

    A gap qualifies only when it lies between two .pdata function ranges and
    every byte in it is INT3 linker padding, so no undeclared code (leaf
    functions, data, generated blobs) can live there.  One INT3 is left on
    each side as a guard.  Only bytes inside .text VirtualSize are used.
    """
    pdata = next((section for section in sections if section[0] == ".pdata"), None)
    if pdata is None:
        raise PatchError("Fallback detour requires PE function boundaries (.pdata)")
    virtual_size = next(section[2] for section in sections if section[0] == ".text")
    limit = text_rva + min(virtual_size, len(text))
    with dll.open("rb") as stream:
        stream.seek(pdata[3])
        entries = stream.read(pdata[4])
    ranges = []
    for offset in range(0, len(entries) - 11, 12):
        start, end, _unwind = struct.unpack_from("<III", entries, offset)
        if text_rva <= start < end <= limit:
            ranges.append((start, end))
    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    caves = []
    for (_start, end), (next_start, _next_end) in zip(merged, merged[1:]):
        length = next_start - end
        if length < minimum + 2:
            continue
        gap = text[end - text_rva:next_start - text_rva]
        if gap.count(0xCC) == len(gap):
            caves.append((end + 1, length - 2))
    return caves


# The output helper, as a small control-flow graph of exact instructions.
# Compilers have started to split it into separately placed fragments joined
# by jumps (Edge 158 places the prologue and the body ~120 MB apart), so it is
# matched by meaning rather than by one contiguous byte string.  Every
# instruction must still match exactly (mnemonic and operands); only the
# placement of blocks and the encoding of jumps between them may differ.
_HELPER_PROGRAM = {
    "entry": (
        "push rsi", "push rdi", "mov rax, rcx", "cmp dl, 2", "je @hdr",
        "@@usage",
    ),
    "usage": (
        "movzx ecx, dl", "cmp ecx, 1", "je @wcg", "test ecx, ecx", "jne @trap",
        "movzx edx, r8b", "jmp @store",
    ),
    "hdr": ("movzx edx, r8b", "or rdx, 4", "jmp @store"),
    "wcg": ("movzx edx, r8b", "or rdx, 2", "@@store"),
    "store": (
        "mov r8, qword ptr [rsp + 0x38]", "mov ecx, edx", "shl ecx, 6",
        "lea edi, [rcx + rdx*4]", "add rdi, rax", "mov ecx, 0x11", "mov rsi, r9",
        "rep movsd dword ptr [rdi], dword ptr [rsi]",
        "mov qword ptr [rax + rdx*8 + 0x198], r8", "pop rdi", "pop rsi", "ret",
    ),
}
_TRAPS = {"ud1", "ud2", "int3"}
_HELPER_ENTRY_SIGNATURE = bytes.fromhex("56 57 48 89 C8 80 FA 02")


def match_output_helper(text: bytes, text_rva: int, entry_rva: int):
    """Return the byte runs of a verified output helper starting at entry_rva.

    Unconditional jumps between blocks are followed transparently.  Returns
    [(rva, bytes)] of every matched instruction (for later verification), or
    None when anything differs from the modeled helper.
    """
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64  # noqa: WPS433

    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    labels = {"entry": entry_rva}
    runs = []
    pending = ["entry"]
    verified = set()

    def decode(rva):
        offset = rva - text_rva
        if not 0 <= offset < len(text):
            return None
        return next(decoder.disasm(text[offset:offset + 16], rva), None)

    def bind(label, rva):
        if label in labels and labels[label] != rva:
            return False
        if label not in labels:
            labels[label] = rva
            pending.append(label)
        return True

    while pending:
        label = pending.pop()
        if label in verified:
            continue
        verified.add(label)
        if label == "trap":
            instruction = decode(labels[label])
            if instruction is None or instruction.mnemonic not in _TRAPS:
                return None
            continue
        rva = labels[label]
        for expected in _HELPER_PROGRAM[label]:
            # Follow placement jumps that the model does not contain.
            for _hop in range(4):
                instruction = decode(rva)
                if instruction is None:
                    return None
                if instruction.mnemonic == "jmp" and not expected.startswith("jmp"):
                    if instruction.op_str.startswith("0x"):
                        rva = int(instruction.op_str, 16)
                        continue
                    return None
                break
            else:
                return None
            if expected.startswith("@@"):
                if not bind(expected[2:], rva):
                    return None
                break
            if " @" in expected:
                mnemonic, _separator, target_label = expected.partition(" @")
                if instruction.mnemonic != mnemonic or not instruction.op_str.startswith("0x"):
                    return None
                if not bind(target_label, int(instruction.op_str, 16)):
                    return None
            elif f"{instruction.mnemonic} {instruction.op_str}".strip() != expected:
                return None
            runs.append((instruction.address, bytes(instruction.bytes)))
            rva = instruction.address + instruction.size
    if set(labels) != set(_HELPER_PROGRAM) | {"trap"}:
        return None
    return runs


def find_output_helpers(text: bytes, text_rva: int):
    """All entries whose control flow matches the modeled output helper."""
    found = []
    position = 0
    while (position := text.find(_HELPER_ENTRY_SIGNATURE, position)) >= 0:
        runs = match_output_helper(text, text_rva, text_rva + position)
        if runs is not None:
            found.append((text_rva + position, runs))
        position += 1
    return found


def discover_output_detour_layout(dll: Path, sections, text=None, text_rva=None) -> dict:
    """Locate the exact output helper and private code caves for its detour."""
    if text is None:
        text, text_rva = _text_section(sections, dll)
    helpers = find_output_helpers(text, text_rva)
    if len(helpers) != 1:
        raise PatchError(
            f"Fallback detour requires one verified output helper; found {len(helpers)}"
        )
    helper, helper_runs = helpers[0]
    contiguous = text[helper - text_rva:helper - text_rva + len(HDR_OUTPUT_HELPER_BYTES)]

    references = 0
    for opcode in (b"\xE8", b"\xE9"):
        position = 0
        while (position := text.find(opcode, position)) >= 0:
            if position + 5 <= len(text):
                target = text_rva + position + 5 + struct.unpack_from("<i", text, position + 1)[0]
                if target == helper:
                    references += 1
            position += 1
    if not 1 <= references <= DETOUR_MAX_HELPER_REFERENCES:
        raise PatchError(
            f"Fallback detour found {references} direct output-helper reference(s)"
        )

    caves = find_padding_caves(dll, sections, text, text_rva)
    caves = [
        cave for cave in caves
        if all(cave[0] + cave[1] <= rva or cave[0] >= rva + len(data) for rva, data in helper_runs)
    ]
    # Prefer caves close to the helper (ordinary code, same locality) and
    # never more than eight separate chunks.
    caves.sort(key=lambda cave: (abs(cave[0] - helper), cave[0]))
    chunks = None
    for count in range(1, min(8, len(caves)) + 1):
        try:
            chunks = build_output_detour_chunks(caves[:count], helper)
            break
        except PatchError:
            continue
    if chunks is None:
        raise PatchError(
            f"Fallback detour found no safe code cave ({len(caves)} padding gap(s) available)"
        )
    stub = []
    for rva, data in chunks:
        original = _read(dll, sections, rva, len(data))
        if original != b"\xCC" * len(data):
            raise PatchError(f"Fallback detour cave at RVA 0x{rva:X} is not pristine padding")
        stub.append((rva, original, data))
    # Everything matched except the prologue the detour replaces must stay
    # byte-identical in the target process.
    helper_checks = [
        (rva, data) for rva, data in helper_runs
        if not helper <= rva < helper + len(HELPER_PROLOGUE)
    ]
    return dict(
        output_helper_rva=helper,
        output_helper_contiguous=contiguous == HDR_OUTPUT_HELPER_BYTES,
        output_helper_checks=helper_checks,
        output_helper_references=references,
        detour_chunks=stub,
        detour_hook_original=HELPER_PROLOGUE,
        detour_hook_patched=b"\xE9" + rel32(helper, 5, chunks[0][0]),
    )


def build_output_detour(stub_rva: int, helper_rva: int) -> bytes:
    """Single-cave form of the detour stub (used by tests and diagnostics)."""
    (_rva, data), = build_output_detour_chunks([(stub_rva, 64)], helper_rva)
    return data
