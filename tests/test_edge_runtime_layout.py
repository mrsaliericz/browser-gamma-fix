"""Exercise Edge discovery on synthetic PE data, without browser binaries."""

from pathlib import Path
import struct
import tempfile
import unittest

import runtime_gamma22 as runtime


class EdgeImage:
    FIRST_INITIALIZER = 0x1100
    GAMUT = 0x6100
    SRGB = 0x6128
    GAMMA22 = 0x6148
    FACTORY = 0x3000
    POINTER = 0x8000
    LOOP = 0x2200
    HELPER = 0x2800
    TABLE = 0x6200

    def __init__(self, count=97):
        # Only the headers/sections consumed by the read-only parser are needed.
        self.data = bytearray(0x9000)
        self.data[:2] = b"MZ"
        struct.pack_into("<I", self.data, 0x3C, 0x80)
        self.put(0x80, b"PE\0\0")
        struct.pack_into("<HH", self.data, 0x84, 0x8664, 3)
        for index, (name, base, size) in enumerate(
            ((b".text", 0x1000, 0x4000), (b".rdata", 0x6000, 0x1000),
             (b".data", 0x8000, 0x1000))
        ):
            offset = 0x98 + index * 40
            self.put(offset, name.ljust(8, b"\0"))
            struct.pack_into("<IIII", self.data, offset + 8, size, base, size, base)
        self.put(0x1000, b"\xCC" * 0x4000)
        self.put(self.GAMUT, runtime.SRGB_GAMUT)
        self.put(self.SRGB, runtime.SRGB_TRANSFER_FUNCTION)
        self.put(self.GAMMA22, runtime.GAMMA22_TRANSFER_FUNCTION)
        self.put(self.FACTORY, b"\xC3")
        self.put(self.HELPER, runtime.HDR_OUTPUT_HELPER_BYTES)
        self.put(self.TABLE, b"\x01\x02\x00")
        for index in range(count):
            base = self.FIRST_INITIALIZER + index * 32
            self.put(base, b"\x48\x8D\x0D" + runtime.rel32(base, 7, self.SRGB))
            self.put(base + 7, b"\x48\x8D\x15" + runtime.rel32(base + 7, 7, self.GAMUT))
            self.put(base + 14, b"\xE8" + runtime.rel32(base + 14, 5, self.FACTORY))
            self.put(base + 19, b"\x48\x89\x05" + runtime.rel32(base + 19, 7, self.POINTER))
        self.add_output_loop(self.LOOP)

    def put(self, address, value):
        self.data[address:address + len(value)] = value

    def add_output_loop(self, base):
        code = bytearray(b"\x45\x31\xC0\x4C\x8D\x8C\x24")
        code += struct.pack("<I", 0x180)
        code += b"\xE8" + runtime.rel32(base + 11, 5, self.HELPER)
        code += b"\x48\x89\x5C\x24\x70\x48\x89\xE9\x8A\x54\x24\x70"
        code += b"\x41\xB0\x01\x4C\x8D\x8C\x24" + struct.pack("<I", 0x180)
        code += b"\xE8" + runtime.rel32(base + 39, 5, self.HELPER)
        code += b"\x48\xFF\xC7\x48\x83\xFF\x02" + b"\xCC" * 11
        code += b"\x48\x8D\x0D" + runtime.rel32(base + 62, 7, self.TABLE)
        code += b"\x8A\x0C\x0F"
        assert len(code) == 72
        self.put(base, code)


class EdgeRuntimeLayoutTests(unittest.TestCase):
    def plan(self, fixture):
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "msedge.dll"
            dll.write_bytes(fixture.data)
            result = runtime.make_runtime_plan(dll)
            self.assertEqual(dll.read_bytes(), fixture.data)
            return result

    def test_known_counts_produce_complete_plan(self):
        for count in (97, 98, 99, 101):
            with self.subTest(count=count):
                fixture = EdgeImage(count)
                plan = self.plan(fixture)
                self.assertEqual(len(plan.layout["initializer_rvas"]), count)
                self.assertEqual(len(plan.writes), count + 2)
                for item in plan.writes[:count]:
                    self.assertEqual(item.patched[:3], b"\x48\x8D\x0D")
                    target = item.rva + 7 + struct.unpack_from("<i", item.patched, 3)[0]
                    self.assertEqual(target, fixture.GAMMA22)
                self.assertEqual(plan.writes[-2].patched, b"\x48\x83\xFF\x03")
                self.assertEqual(plan.writes[-1].patched, b"\x00\x01\x02")

    def test_unverified_counts_are_rejected(self):
        for count in (0, 1, 96, 100, 102):
            with self.subTest(count=count), self.assertRaisesRegex(
                runtime.PatchError, "Unexpected Edge singleton constructors"
            ):
                self.plan(EdgeImage(count))

    def test_mixed_factory_or_pointer_is_rejected(self):
        for field in ("factory", "pointer"):
            with self.subTest(field=field):
                fixture = EdgeImage()
                if field == "factory":
                    base = fixture.FIRST_INITIALIZER + 14
                    fixture.put(base + 1, runtime.rel32(base, 5, fixture.FACTORY + 16))
                else:
                    base = fixture.FIRST_INITIALIZER + 19
                    fixture.put(base + 3, runtime.rel32(base, 7, fixture.POINTER + 16))
                with self.assertRaisesRegex(runtime.PatchError, "factory/pointer tuple"):
                    self.plan(fixture)

    def test_partial_98_layout_is_not_accepted_as_97(self):
        # Both changes leave 97 valid constructors. The completeness checks
        # must catch the remaining unmatched sRGB load or singleton store.
        for field, error in (("gamut", "initializer load"), ("transfer", "initializer store")):
            with self.subTest(field=field):
                fixture = EdgeImage(98)
                base = fixture.FIRST_INITIALIZER
                if field == "gamut":
                    fixture.put(base + 10, runtime.rel32(base + 7, 7, fixture.GAMUT + 4))
                else:
                    fixture.put(base + 3, runtime.rel32(base, 7, fixture.GAMMA22))
                with self.assertRaisesRegex(runtime.PatchError, error):
                    self.plan(fixture)

    def test_missing_or_ambiguous_constants_are_rejected(self):
        for variant in ("missing", "duplicate"):
            with self.subTest(variant=variant):
                fixture = EdgeImage()
                if variant == "missing":
                    fixture.data[fixture.GAMMA22] ^= 1
                else:
                    fixture.put(0x6300, fixture.data[fixture.GAMUT:fixture.GAMMA22 + 28])
                with self.assertRaisesRegex(runtime.PatchError, "constant block"):
                    self.plan(fixture)

    def test_invalid_output_loop_or_table_is_rejected(self):
        for address in (EdgeImage.LOOP + 50, EdgeImage.TABLE):
            with self.subTest(address=address):
                fixture = EdgeImage()
                fixture.data[address] ^= 1
                with self.assertRaisesRegex(runtime.PatchError, "output loop"):
                    self.plan(fixture)

    def test_multiple_output_loops_are_rejected(self):
        fixture = EdgeImage()
        fixture.add_output_loop(0x2400)
        with self.assertRaisesRegex(runtime.PatchError, "output loop; found 2"):
            self.plan(fixture)

    def test_shared_color_constants_are_not_patch_targets(self):
        fixture = EdgeImage()
        plan = self.plan(fixture)
        self.assertEqual(len(plan.checks), 4)
        for label, rva, expected in plan.checks:
            self.assertEqual(fixture.data[rva:rva + len(expected)], expected, label)
            for item in plan.writes:
                self.assertTrue(
                    item.rva + len(item.patched) <= rva
                    or rva + len(expected) <= item.rva,
                    f"{item.label} overlaps {label}",
                )


if __name__ == "__main__":
    unittest.main()
