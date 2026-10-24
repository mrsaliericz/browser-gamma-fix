"""Bounded symbolic verification of split Edge output loops (read-only).

Instruction addresses come from PE exception-directory function boundaries.
Only a small, explicitly modeled instruction subset is accepted. Unknown
instructions, branches, values or ambiguous loops reject the candidate.
"""
from collections import deque
from bisect import bisect_right
import struct

from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_REG, X86_OP_IMM, X86_OP_MEM

from gamma22_patcher import HDR_OUTPUT_HELPER_BYTES, PatchError, rva_to_offset


def family(ins, reg):
    name = ins.reg_name(reg)
    aliases = {'eax':'rax','ax':'rax','al':'rax','ecx':'rcx','cx':'rcx','cl':'rcx',
               'edx':'rdx','dx':'rdx','dl':'rdx','ebx':'rbx','bx':'rbx','bl':'rbx',
               'edi':'rdi','di':'rdi','dil':'rdi','esi':'rsi','si':'rsi','sil':'rsi'}
    if name.startswith('r') and name[1:-1].isdigit() and name[-1] in 'dbw':
        return name[:-1]
    return aliases.get(name, name)


def find_unused_int3_cave(dll, sections, text, text_rva, target_rva, size=16):
    """Return nearby INT3 padding outside every PE function body.

    Edge 156 keeps its two-byte usage table immediately before unrelated data,
    so extending it would corrupt that data.  A reserved INT3 alignment gap is
    a safer private-process home for the three-byte replacement table.
    """
    pdata = next((section for section in sections if section[0] == '.pdata'), None)
    if pdata is None:
        raise PatchError('Edge usage-table relocation requires PE function boundaries')
    with dll.open('rb') as stream:
        stream.seek(pdata[3])
        entries = stream.read(pdata[4])

    # An INT3 run inside a declared function can be reachable cold code. Only
    # consider linker padding outside every exception-directory function range.
    ranges = []
    for offset in range(0, len(entries) - 11, 12):
        start, end, _ = struct.unpack_from('<III', entries, offset)
        if text_rva <= start < end <= text_rva + len(text):
            ranges.append((start, end))
    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    starts = [start for start, _end in merged]

    candidates = []
    position = 0
    needle = b'\xcc' * size
    while (position := text.find(needle, position)) >= 0:
        rva = text_rva + position
        index = bisect_right(starts, rva) - 1
        overlaps_previous = index >= 0 and merged[index][1] > rva
        overlaps_next = index + 1 < len(merged) and merged[index + 1][0] < rva + size
        if not overlaps_previous and not overlaps_next:
            candidates.append(rva)
        position += size
    if not candidates:
        raise PatchError('Edge usage-table relocation found no safe INT3 padding')
    return min(candidates, key=lambda rva: (abs(rva - target_rva), rva))


def find_compact_output_loop(dll, sections, text, text_rva):
    """Verify Edge 154's compact split ScreenWin output loop.

    This compiler variant keeps the two output-helper calls and the SDR/WCG
    usage table in a cold function fragment. It is deliberately accepted only
    when every relationship below is unique and structurally exact.
    """
    helpers = []
    position = 0
    while (position := text.find(HDR_OUTPUT_HELPER_BYTES, position)) >= 0:
        helpers.append(text_rva + position)
        position += 1
    if len(helpers) != 1:
        raise PatchError('Compact Edge analysis requires one exact output helper')
    helper = helpers[0]

    def call_target(position):
        if position + 5 > len(text) or text[position] != 0xE8:
            return None
        return text_rva + position + 5 + struct.unpack_from('<i', text, position + 1)[0]

    def read(rva, size):
        with dll.open('rb') as stream:
            stream.seek(rva_to_offset(sections, rva))
            return stream.read(size)

    candidates = []
    position = 0
    loop_prefix = b'\x48\xff\xc7\x48\x83\xff\x02'
    while (position := text.find(loop_prefix, position)) >= 0:
        # inc rdi; cmp rdi, 2; je; call; lea rcx, [rip + usage table];
        # mov cl, [rdi + rcx]; mov [rsp + disp8], cl; cmp eax, 12; jl.
        code = text[position:position + 72]
        if code[7:8] == b'\x74':
            jump_size = 2
        elif code[7:9] == b'\x0f\x84':
            jump_size = 6
        else:
            position += 1
            continue
        call_offset = 7 + jump_size
        lea_offset = call_offset + 5
        load_offset = lea_offset + 7
        store_offset = load_offset + 3
        compare_offset = store_offset + 4
        if (
            len(code) < compare_offset + 4
            or code[:7] != loop_prefix[:7]
            or call_target(position + call_offset) is None
            or code[lea_offset:lea_offset + 3] != b'\x48\x8d\x0d'
            or code[load_offset:load_offset + 3] != b'\x8a\x0c\x0f'
            or code[store_offset:store_offset + 3] != b'\x88\x4c\x24'
            or code[compare_offset:compare_offset + 3] != b'\x83\xf8\x0c'
            or code[compare_offset + 3] != 0x7C
        ):
            position += 1
            continue
        table = (
            text_rva + position + lea_offset + 7
            + struct.unpack_from('<i', code, lea_offset + 3)[0]
        )
        if read(table, 3) != b'\x01\x02\x00':
            position += 1
            continue

        # The immediately preceding fragment must make exactly two calls to the
        # one verified helper: output plane 0 then output plane 1. Both calls
        # must consume the byte saved from the same usage-table stack slot.
        call_rvas = [
            text_rva + candidate
            for candidate in range(max(0, position - 80), position)
            if call_target(candidate) == helper
        ]
        if len(call_rvas) != 2:
            position += 1
            continue
        call_positions = [rva - text_rva for rva in call_rvas]
        first_context = text[max(0, call_positions[0] - 24):call_positions[0]]
        second_context = text[max(0, call_positions[1] - 24):call_positions[1]]
        usage_stack_offset = code[store_offset + 3]
        usage_load = b'\x8a\x54\x24' + bytes((usage_stack_offset,))
        if (
            b'\x45\x31\xc0' not in first_context
            or b'\x41\xb0\x01' not in second_context
            or usage_load not in first_context
            or usage_load not in second_context
        ):
            position += 1
            continue
        candidates.append((text_rva + position + 3, table, helper))
        position += 1

    if len(candidates) != 1:
        raise PatchError(
            f'Expected one verified compact Edge output loop; found {len(candidates)}'
        )
    loop_limit_rva, usage_table_rva, output_helper_rva = candidates[0]
    return dict(
        loop_limit_rva=loop_limit_rva,
        usage_table_rva=usage_table_rva,
        output_helper_rva=output_helper_rva,
        loop_context=b'',
        loop_original=b'\x48\x83\xff\x02',
        loop_patched=b'\x48\x83\xff\x03',
    )


def find_fused_output_loop(dll, sections, text, text_rva):
    """Verify Edge 156's fused ScreenWin output loop.

    This variant keeps both output-helper calls, the count limit and the usage
    table in one large function instead of a dedicated cold fragment.  It is
    accepted only when one function proves the two output planes, the table
    flow, both format paths and the loop exit relationship.
    """
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True

    helpers = []
    pos = 0
    while (pos := text.find(HDR_OUTPUT_HELPER_BYTES, pos)) >= 0:
        helpers.append(text_rva + pos)
        pos += 1
    if len(helpers) != 1:
        raise PatchError('Fused Edge analysis requires one exact output helper')
    helper = helpers[0]

    # Avoid decoding every function in a large Edge DLL. Locate direct helper
    # calls from bytes first, then disassemble only their candidate functions.
    calls_to_helper = []
    pos = 0
    while (pos := text.find(b'\xe8', pos)) >= 0:
        if (pos + 5 <= len(text)
                and text_rva + pos + 5 + struct.unpack_from('<i', text, pos + 1)[0] == helper):
            calls_to_helper.append(text_rva + pos)
        pos += 1

    pdata = next((s for s in sections if s[0] == '.pdata'), None)
    if pdata is None:
        raise PatchError('Fused Edge analysis requires PE function boundaries')

    def stack_slot(ins, operand):
        if operand.type != X86_OP_MEM:
            return None
        memory = operand.mem
        if ins.reg_name(memory.base) != 'rsp' or memory.index:
            return None
        return memory.disp

    def call_target(ins):
        if ins.mnemonic != 'call' or len(ins.operands) != 1:
            return None
        return ins.operands[0].imm if ins.operands[0].type == X86_OP_IMM else None

    def helper_call_signature(instructions, call_index):
        """Return only the directly established helper arguments."""
        rcx_slot = rdx_slot = stack_arg = None
        r8_value = r9_kind = None
        for ins in instructions[max(0, call_index - 9):call_index]:
            ops = ins.operands
            if len(ops) != 2:
                continue
            destination = family(ins, ops[0].reg) if ops[0].type == X86_OP_REG else None
            if (ins.mnemonic == 'lea' and destination == 'rcx'
                    and (slot := stack_slot(ins, ops[1])) is not None):
                rcx_slot = slot
            elif (ins.mnemonic == 'mov' and destination == 'rdx'
                    and (slot := stack_slot(ins, ops[1])) is not None
                    and ops[1].size == 1):
                rdx_slot = slot
            elif (ins.mnemonic == 'xor' and destination == 'r8'
                    and ops[1].type == X86_OP_REG
                    and family(ins, ops[1].reg) == 'r8'):
                r8_value = 0
            elif (ins.mnemonic == 'mov' and destination == 'r8'
                    and ops[1].type == X86_OP_IMM):
                r8_value = ops[1].imm
            elif ins.mnemonic == 'lea' and destination == 'r9' and stack_slot(ins, ops[1]) is not None:
                r9_kind = 'stack'
            elif ins.mnemonic == 'mov' and destination == 'r9' and ops[1].type == X86_OP_REG:
                r9_kind = 'register'
            elif (ins.mnemonic == 'mov' and (slot := stack_slot(ins, ops[0])) == 32
                    and ops[1].type == X86_OP_REG):
                stack_arg = family(ins, ops[1].reg)
        return rcx_slot, rdx_slot, r8_value, r9_kind, stack_arg

    with dll.open('rb') as stream:
        stream.seek(pdata[3])
        entries = stream.read(pdata[4])

        def read(rva, size):
            stream.seek(rva_to_offset(sections, rva))
            return stream.read(size)

        results = []
        for offset in range(0, len(entries) - 11, 12):
            start, end, _ = struct.unpack_from('<III', entries, offset)
            if not (text_rva <= start < end <= text_rva + len(text)) or end - start > 65536:
                continue
            function_calls = [call for call in calls_to_helper if start <= call < end]
            if len(function_calls) != 2:
                continue
            code = text[start - text_rva:end - text_rva]
            instructions = list(decoder.disasm(code, start))
            helper_calls = [
                index for index, ins in enumerate(instructions)
                if call_target(ins) == helper
            ]
            if len(helper_calls) != 2:
                continue
            first_call, second_call = helper_calls
            first = helper_call_signature(instructions, first_call)
            second = helper_call_signature(instructions, second_call)
            if not (
                first[2] == 0 and second[2] == 1
                and first[0] is not None and first[0] == second[0]
                and first[1] is not None and first[1] == second[1]
                and first[3] in ('stack', 'register') and second[3] in ('stack', 'register')
                and first[4] is not None and second[4] is not None
            ):
                continue

            for index, inc in enumerate(instructions[:-9]):
                compare, exit_jump, query, table_lea, table_load, table_store, format_compare, format_jump = instructions[index + 1:index + 9]
                if not (
                    inc.mnemonic == 'inc' and len(inc.operands) == 1 and inc.operands[0].type == X86_OP_REG
                    and compare.mnemonic == 'cmp' and len(compare.operands) == 2
                    and compare.operands[0].type == X86_OP_REG and compare.operands[1].type == X86_OP_IMM
                    and family(inc, inc.operands[0].reg) == family(compare, compare.operands[0].reg)
                    and compare.operands[1].imm == 2 and compare.imm_size == 1
                    and exit_jump.mnemonic == 'je' and len(exit_jump.operands) == 1 and exit_jump.operands[0].type == X86_OP_IMM
                    and query.mnemonic == 'call' and query.operands[0].type == X86_OP_IMM
                    and table_lea.mnemonic == 'lea' and table_lea.operands[0].type == X86_OP_REG
                    and table_lea.operands[1].type == X86_OP_MEM and table_lea.reg_name(table_lea.operands[1].mem.base) == 'rip'
                    and table_load.mnemonic == 'mov' and table_load.operands[0].type == X86_OP_REG and table_load.operands[1].type == X86_OP_MEM
                    and table_store.mnemonic == 'mov' and table_store.operands[0].type == X86_OP_MEM and table_store.operands[1].type == X86_OP_REG
                    and format_compare.mnemonic == 'cmp' and len(format_compare.operands) == 2
                    and format_compare.operands[0].type == X86_OP_REG and family(format_compare, format_compare.operands[0].reg) == 'rax'
                    and format_compare.operands[1].type == X86_OP_IMM and format_compare.operands[1].imm == 12
                    and format_jump.mnemonic == 'jl' and len(format_jump.operands) == 1 and format_jump.operands[0].type == X86_OP_IMM
                ):
                    continue
                table_reg = family(table_lea, table_lea.operands[0].reg)
                index_reg = family(inc, inc.operands[0].reg)
                load_memory = table_load.operands[1].mem
                if not (
                    family(table_load, table_load.operands[0].reg) == table_reg
                    and {family(table_load, load_memory.base), family(table_load, load_memory.index)} == {index_reg, table_reg}
                    and load_memory.scale == 1 and load_memory.disp == 0
                    and stack_slot(table_store, table_store.operands[0]) == first[1]
                    and family(table_store, table_store.operands[1].reg) == table_reg
                ):
                    continue
                table = table_lea.address + table_lea.size + table_lea.operands[1].mem.disp
                table_bytes = read(table, 3)
                relocation = None
                if table_bytes == b'\x01\x02\x00':
                    usage_table_rva = table
                    usage_table_original = table_bytes
                elif table_bytes[:2] == b'\x01\x02':
                    # This compiler layout puts unrelated bytes immediately
                    # after the two entries. Relocate the replacement table
                    # into verified linker padding instead of overwriting it.
                    original_lea = bytes(table_lea.bytes)
                    if len(original_lea) != 7 or original_lea[:3] != b'\x48\x8d\x0d':
                        continue
                    try:
                        usage_table_rva = find_unused_int3_cave(
                            dll, sections, text, text_rva, table_lea.address
                        )
                    except PatchError:
                        continue
                    usage_table_original = read(usage_table_rva, 3)
                    if usage_table_original != b'\xcc' * 3:
                        continue
                    displacement = usage_table_rva - (table_lea.address + table_lea.size)
                    if not -(1 << 31) <= displacement < (1 << 31):
                        continue
                    relocation = (
                        table_lea.address,
                        original_lea,
                        b'\x48\x8d\x0d' + struct.pack('<i', displacement),
                        usage_table_rva,
                    )
                else:
                    continue
                if not (compare.address > instructions[second_call].address
                        and start < exit_jump.operands[0].imm < end
                        and start <= format_jump.operands[0].imm <= instructions[first_call].address
                        and instructions[first_call].address - format_jump.operands[0].imm <= 48):
                    continue
                fallback_jump = next(
                    (ins for ins in instructions[index + 8:]
                     if ins.mnemonic == 'jmp' and len(ins.operands) == 1
                     and ins.operands[0].type == X86_OP_IMM
                     and ins.operands[0].imm == instructions[first_call].address),
                    None,
                )
                if fallback_jump is None:
                    continue
                prefix_start = format_jump.operands[0].imm
                prefix = code[prefix_start - start:compare.address - start]
                suffix = code[compare.address + compare.size - start:fallback_jump.address + fallback_jump.size - start]
                if not prefix or not suffix:
                    continue
                semantic_checks = [
                    ('Edge fused output prefix', prefix_start, prefix),
                    ('Edge fused output suffix', compare.address + compare.size, suffix),
                ]
                if relocation:
                    _lea_rva, _original_lea, _patched_lea, cave_rva = relocation
                    semantic_checks.extend((
                        ('Edge fused source usage table', table, b'\x01\x02'),
                        ('Edge fused usage-table cave tail', cave_rva + 3, b'\xcc' * 13),
                    ))
                results.append(dict(
                    loop_limit_rva=compare.address,
                    usage_table_rva=usage_table_rva,
                    usage_table_original=usage_table_original,
                    output_helper_rva=helper,
                    loop_context=b'',
                    loop_original=bytes(compare.bytes),
                    loop_patched=(bytes(compare.bytes[:compare.imm_offset]) + b'\x03'
                                  + bytes(compare.bytes[compare.imm_offset + 1:])),
                    semantic_checks=semantic_checks,
                    usage_table_relocation=relocation,
                ))
        unique = {result['loop_limit_rva']: result for result in results}
        if len(unique) != 1 or len(results) != 1:
            raise PatchError(f'Expected one verified fused Edge output loop; found {len(results)}')
        return next(iter(unique.values()))


def find_split_output_loop(dll, sections, text, text_rva):
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    helpers = []
    pos = 0
    while (pos := text.find(HDR_OUTPUT_HELPER_BYTES, pos)) >= 0:
        helpers.append(text_rva + pos)
        pos += 1
    if len(helpers) != 1:
        raise PatchError('Semantic Edge analysis requires one exact output helper')
    helper = helpers[0]
    pdata = next((s for s in sections if s[0] == '.pdata'), None)
    if pdata is None:
        raise PatchError('Edge output loop analysis requires PE function boundaries')
    with dll.open('rb') as stream:
        stream.seek(pdata[3])
        entries = stream.read(pdata[4])
        def read(rva, size):
            stream.seek(rva_to_offset(sections, rva))
            return stream.read(size)

        calls_to_helper = []
        pos = 0
        while (pos := text.find(b'\xe8', pos)) >= 0:
            if pos + 5 <= len(text) and text_rva+pos+5+struct.unpack_from('<i',text,pos+1)[0] == helper:
                calls_to_helper.append(text_rva+pos)
            pos += 1
        results = []
        for offset in range(0, len(entries) - 11, 12):
            start, end, _ = struct.unpack_from('<III', entries, offset)
            if not (text_rva <= start < end <= text_rva + len(text)) or end-start > 65536:
                continue
            code = text[start-text_rva:end-text_rva]
            # Cheap prefilter only; instruction boundaries are decoded below.
            if not any(start <= call < end for call in calls_to_helper):
                continue
            instructions = list(decoder.disasm(code, start))
            by_address = {i.address:i for i in instructions}
            for n, zero in enumerate(instructions[:-2]):
                lea, jump = instructions[n+1:n+3]
                if not (zero.mnemonic == 'xor' and len(zero.operands)==2
                        and all(o.type==X86_OP_REG for o in zero.operands)
                        and zero.operands[0].reg==zero.operands[1].reg
                        and lea.mnemonic=='lea' and lea.operands[1].type==X86_OP_MEM
                        and lea.reg_name(lea.operands[1].mem.base)=='rip'
                        and jump.mnemonic=='jmp' and jump.operands[0].type==X86_OP_IMM):
                    continue
                index = family(zero,zero.operands[0].reg)
                table_reg = family(lea,lea.operands[0].reg)
                if index == table_reg or zero.operands[0].size not in (4,8):
                    continue
                table = lea.address+lea.size+lea.operands[1].mem.disp
                if read(table,3) != b'\x01\x02\x00':
                    continue
                regs = {index:('index',),table_reg:('table',)}
                # Resolve nonvolatile input addresses/constants from the nearest
                # preceding write; never skip over an unmodeled writer.
                for reg in ('rbx','rbp','rsi','rdi','r12','r13','r14','r15'):
                    if reg in regs:
                        continue
                    for prev in reversed(instructions[max(0,n-80):n]):
                        if prev.mnemonic.startswith('j') or prev.mnemonic in ('call','ret'):
                            break
                        if reg not in {family(prev,r) for r in prev.regs_access()[1]}:
                            continue
                        ops=prev.operands
                        if prev.mnemonic=='lea' and len(ops)==2 and ops[1].type==X86_OP_MEM and prev.reg_name(ops[1].mem.base)=='rsp' and not ops[1].mem.index:
                            regs[reg]=('stack',ops[1].mem.disp)
                        elif prev.mnemonic in ('mov','movabs') and len(ops)==2 and ops[1].type==X86_OP_IMM:
                            regs[reg]=ops[1].imm
                        break
                try:
                    limit = verify_paths(by_address,jump.operands[0].imm,regs,index,helper)
                except ValueError:
                    continue
                # Keep all decoded function bytes as evidence, excluding the
                # mutable loop limit. This also covers resolved input writers.
                checks = [("Edge semantic function prefix",start,code[:limit.address-start]),
                          ("Edge semantic function suffix",limit.address+limit.size,code[limit.address+limit.size-start:])]
                results.append(dict(loop_limit_rva=limit.address,usage_table_rva=table,
                                    output_helper_rva=helper,loop_context=b'',
                                    loop_original=bytes(limit.bytes),
                                    loop_patched=bytes(limit.bytes[:limit.imm_offset])+b'\x03'+bytes(limit.bytes[limit.imm_offset+1:]),
                                    semantic_checks=checks))
        unique = {r['loop_limit_rva']:r for r in results}
        if len(unique)!=1 or len(results)!=1:
            raise PatchError(f'Expected one verified semantic Edge output loop; found {len(results)}')
        return next(iter(unique.values()))


def verify_paths(code, entry, initial, index, helper):
    queue=deque([(entry,dict(initial),{},[],set())])
    ends=[]
    steps=0
    while queue:
        pc,regs,stack,calls,visited=queue.popleft()
        while True:
            steps+=1
            if steps>256 or pc in visited or pc not in code:
                raise ValueError('unbounded or external control flow')
            visited.add(pc)
            ins=code[pc]; ops=ins.operands; pc+=ins.size
            def address(op):
                m=op.mem
                if ins.reg_name(m.base)=='rsp' and not m.index:
                    return ('stack',m.disp)
                values={regs.get(family(ins,r)) for r in (m.base,m.index) if r}
                if values=={('index',),('table',)} and m.scale==1 and m.disp==0:
                    return ('usage-address',)
                raise ValueError('unknown address')
            def value(op):
                if op.type==X86_OP_IMM: return op.imm
                if op.type==X86_OP_REG:
                    result=regs.get(family(ins,op.reg))
                    if result is None: raise ValueError('unknown register')
                    return result
                a=address(op)
                if a==('usage-address',) and op.size==1: return ('usage',)
                if a in stack: return stack[a]
                raise ValueError('unknown load')
            if ins.mnemonic in ('mov','movabs','lea'):
                result=address(ops[1]) if ins.mnemonic=='lea' else value(ops[1])
                if ops[0].type==X86_OP_REG: regs[family(ins,ops[0].reg)]=result
                elif ops[0].type==X86_OP_MEM: stack[address(ops[0])]=result
                else: raise ValueError('unsupported destination')
            elif ins.mnemonic=='xor' and ops[0].type==X86_OP_REG and ops[1].type==X86_OP_REG and ops[0].reg==ops[1].reg:
                regs[family(ins,ops[0].reg)]=0
            elif ins.mnemonic=='call' and ops[0].type==X86_OP_IMM:
                if ops[0].imm==helper:
                    args=tuple(regs.get(k) for k in ('rcx','rdx','r8','r9'))
                    if not (isinstance(args[0],tuple) and args[0][0]=='stack'
                            and args[1]==('usage',) and args[2]==len(calls)
                            and isinstance(args[3],tuple) and args[3][0]=='stack'
                            and stack.get(('stack',32)) in (0xe00000001,0x900000001)):
                        raise ValueError('incorrect output arguments')
                    calls.append(args)
                    if len(calls)>2: raise ValueError('extra output call')
                elif calls:
                    raise ValueError('unknown call after output')
                for reg in ('rax','rcx','rdx','r8','r9','r10','r11'): regs.pop(reg,None)
            elif ins.mnemonic=='inc' and ops[0].type==X86_OP_REG and family(ins,ops[0].reg)==index and regs.get(index)==('index',):
                regs[index]=('index-plus-one',)
            elif ins.mnemonic=='cmp':
                if ops[0].type==X86_OP_REG and family(ins,ops[0].reg)==index:
                    if not (ops[1].type==X86_OP_IMM and ops[1].imm==2 and ins.imm_size==1
                            and regs.get(index)==('index-plus-one',) and len(calls)==2
                            and calls[0][0]==calls[1][0]):
                        raise ValueError('invalid loop termination')
                    branch=code.get(pc)
                    if not (branch and branch.mnemonic=='je' and branch.operands[0].type==X86_OP_IMM
                            and branch.operands[0].imm in code and pc+branch.size==entry):
                        raise ValueError('invalid loop back edge')
                    ends.append(ins); break
                if not (ops[0].type==X86_OP_REG and family(ins,ops[0].reg)=='rax'
                        and ops[1].type==X86_OP_IMM):
                    raise ValueError('unmodeled condition')
                branch=code.get(pc)
                if not (branch and branch.mnemonic in ('jl','jge') and branch.operands[0].type==X86_OP_IMM):
                    raise ValueError('unmodeled branch')
                queue.append((branch.operands[0].imm,dict(regs),dict(stack),list(calls),set(visited)))
                pc+=branch.size
            elif ins.mnemonic=='jmp' and ops[0].type==X86_OP_IMM:
                pc=ops[0].imm
            else:
                raise ValueError('unmodeled instruction')
    if len(ends)!=2 or len({i.address for i in ends})!=1:
        raise ValueError('expected both output format paths')
    return ends[0]
