"""Control-flow fixtures for Edge output analysis, without browser binaries."""
import struct
import unittest

import test_edge_runtime_layout as fixtures
import runtime_gamma22 as runtime


def split_fixture(stack_shift=0, index_variant=False):
    image=fixtures.EdgeImage()
    image.put(image.LOOP,b'\xcc'*72)
    struct.pack_into('<H',image.data,0x86,4)
    h=0x98+3*40
    image.put(h,b'.pdata\0\0')
    struct.pack_into('<IIII',image.data,h+8,12,0x8800,12,0x8800)
    base=0x3400
    code=bytearray(); labels={}; refs=[]
    def emit(hexstr): code.extend(bytes.fromhex(hexstr))
    def label(name): labels[name]=base+len(code)
    def target(opcode,name):
        emit(opcode); refs.append((len(code),name)); code.extend(b'\0'*4)
    def lea(reg,slot):
        emit('48 8d 8c 24' if reg=='rcx' else '4c 8d 8c 24')
        code.extend(struct.pack('<I',slot+stack_shift))
    def setup(mode,alternate=False):
        emit('4c 89 64 24 20' if mode or alternate else '48 89 5c 24 20')
        lea('rcx',0x240); emit('8a 54 24 2a')
        emit('41 b0 01' if mode else '45 31 c0')
        if mode or alternate: lea('r9',0x670)
        else: emit('4d 89 f1')
    emit('48 bb 01 00 00 00 0e 00 00 00')
    emit('49 bc 01 00 00 00 09 00 00 00')
    emit('4c 8d b4 24'); code.extend(struct.pack('<I',0x628+stack_shift))
    emit('31 f6' if index_variant else '31 ff')
    target('4c 8d 3d',image.TABLE)
    target('e9','header')
    label('primary'); setup(0)
    label('first-call'); target('e8',image.HELPER)
    setup(1); label('second-call'); target('e8',image.HELPER)
    emit('48 ff c6' if index_variant else '48 ff c7')
    label('limit'); emit('48 83 fe 02' if index_variant else '48 83 ff 02')
    target('0f 84','exit')
    label('header'); target('e8',image.FACTORY)
    emit('42 8a 0c 3e' if index_variant else '42 8a 0c 3f')
    emit('88 4c 24 2a 83 f8 0c')
    target('0f 8c','primary')
    setup(0,True); target('e9','first-call')
    label('exit'); emit('c3')
    for offset,name in refs:
        dest=labels[name] if isinstance(name,str) else name
        struct.pack_into('<i',code,offset,dest-(base+offset+4))
    image.put(base,code)
    struct.pack_into('<III',image.data,0x8800,base,base+len(code),0)
    image.labels=labels
    return image


def fused_fixture(two_byte_table=True):
    """Model Edge 156's one-function output loop with a packed table."""
    image = fixtures.EdgeImage(100)
    image.put(image.LOOP, b'\xcc' * 72)
    # Add one exception-directory entry for the fused function.
    struct.pack_into('<H', image.data, 0x86, 4)
    header = 0x98 + 3 * 40
    image.put(header, b'.pdata\0\0')
    struct.pack_into('<IIII', image.data, header + 8, 12, 0x8800, 12, 0x8800)
    base = 0x3400
    code = bytearray()
    labels = {}
    refs = []

    def emit(hexstr):
        code.extend(bytes.fromhex(hexstr))

    def label(name):
        labels[name] = base + len(code)

    def target(opcode, name):
        emit(opcode)
        refs.append((len(code), name))
        code.extend(b'\0' * 4)

    def call(name):
        target('e8', name)

    # The two verified helper calls establish plane 0 and plane 1, then the
    # loop updates the same stack byte from the usage table.
    label('primary')
    emit('4c 89 7c 24 20 48 8d 8c 24 50 02 00 00 8a 54 24 30')
    emit('45 31 c0 4d 89 e1')
    label('first-call'); call(image.HELPER)
    emit('4c 89 74 24 20 48 8d 8c 24 50 02 00 00 8a 54 24 30')
    emit('41 b0 01 4c 8d 8c 24 80 06 00 00')
    label('second-call'); call(image.HELPER)
    emit('48 ff c7')
    label('limit'); emit('48 83 ff 02')
    target('0f 84', 'exit')
    call(image.FACTORY)
    target('48 8d 0d', image.TABLE)
    emit('8a 0c 0f 88 4c 24 30 83 f8 0c')
    target('0f 8c', 'primary')
    emit('4c 89 74 24 20 48 8d 8c 24 50 02 00 00 8a 54 24 30')
    emit('45 31 c0 4c 8d 8c 24 80 06 00 00')
    target('e9', 'first-call')
    label('exit'); emit('c3')
    for offset, name in refs:
        dest = labels[name] if isinstance(name, str) else name
        struct.pack_into('<i', code, offset, dest - (base + offset + 4))
    image.put(base, code)
    struct.pack_into('<III', image.data, 0x8800, base, base + len(code), 0)
    image.labels = labels
    image.put(image.TABLE, b'\x01\x02G' if two_byte_table else b'\x01\x02\x00')
    return image


class SemanticOutputTests(unittest.TestCase):
    plan=fixtures.EdgeRuntimeLayoutTests.plan

    def test_stack_offsets_and_loop_register_can_change(self):
        for shift,index in ((0,False),(0x100,False),(0x80,True)):
            with self.subTest(shift=shift,index=index):
                image=split_fixture(shift,index)
                plan=self.plan(image)
                self.assertEqual(len(plan.writes),99)
                self.assertEqual(plan.writes[-1].rva,image.labels['limit'])
                self.assertEqual(plan.writes[-1].patched[-1],3)
                self.assertEqual(plan.writes[-2].rva,image.TABLE)
                self.assertEqual(len(plan.checks),6)

    def test_wrong_call_table_counter_and_control_flow_rejected(self):
        for variant in ('call','table','counter','exit','format','unknown'):
            with self.subTest(variant=variant):
                image=split_fixture()
                if variant=='call': image.data[image.labels['second-call']+1]^=1
                elif variant=='table': image.data[image.TABLE]=0
                elif variant=='counter': image.data[image.labels['limit']+3]=3
                elif variant=='exit': image.data[image.labels['limit']+5]=0x85
                elif variant=='format':
                    start=image.labels['first-call']; end=image.labels['second-call']
                    pos=image.data.find(bytes.fromhex('41 b0 01'),start,end)
                    image.data[pos+2]=0
                else: image.data[image.labels['primary']]=0xcc
                with self.assertRaises(runtime.PatchError): self.plan(image)

    def test_evidence_excludes_only_mutable_loop_instruction(self):
        image=split_fixture(); plan=self.plan(image)
        for label,rva,data in plan.checks:
            self.assertEqual(bytes(image.data[rva:rva+len(data)]),data,label)
            for write in plan.writes:
                self.assertTrue(write.rva+len(write.patched)<=rva or rva+len(data)<=write.rva)

    def test_fused_loop_relocates_packed_usage_table_to_safe_padding(self):
        image = fused_fixture(two_byte_table=True)
        plan = self.plan(image)
        self.assertEqual(len(plan.layout['initializer_rvas']), 100)
        self.assertIsNotNone(plan.layout['usage_table_relocation'])
        self.assertNotEqual(plan.layout['usage_table_rva'], image.TABLE)
        self.assertEqual(plan.writes[-3].label, 'ScreenWin usage table')
        self.assertEqual(plan.writes[-2].label, 'ScreenWin usage table relocation')
        self.assertEqual(plan.writes[-1].label, 'ScreenWin usage loop limit')
        self.assertEqual(plan.writes[-3].original, b'\xcc' * 3)
        self.assertEqual(plan.writes[-3].patched, b'\0\1\2')
        self.assertEqual(image.data[image.TABLE:image.TABLE + 3], b'\1\2G')

    def test_fused_loop_rejects_a_changed_packed_table_prefix(self):
        image = fused_fixture(two_byte_table=True)
        image.data[image.TABLE] = 0
        with self.assertRaises(runtime.PatchError):
            self.plan(image)


if __name__=='__main__': unittest.main()
