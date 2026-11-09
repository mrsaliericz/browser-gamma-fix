"""Layout-independent fallbacks used when a browser update changes codegen."""

import ctypes
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import hot_attach_gamma22 as hot
import resilient_layout as resilient
import runtime_gamma22 as runtime
import test_edge_runtime_layout as fixtures


def with_pdata(image, functions):
    """Add a .pdata section that declares the given (start, end) functions."""
    struct.pack_into("<H", image.data, 0x86, 4)
    header = 0x98 + 3 * 40
    image.put(header, b".pdata\0\0")
    struct.pack_into("<IIII", image.data, header + 8, 0x200, 0x8800, 0x200, 0x8800)
    image.put(0x8800, b"\0" * 0x200)
    for index, (start, end) in enumerate(functions):
        struct.pack_into("<III", image.data, 0x8800 + index * 12, start, end, 0)
    return image


def edge_image(count=97, *, broken_loop=False, pdata=True):
    image = fixtures.EdgeImage(count)
    if broken_loop:
        # Same mutation the strict suite rejects: the loop compare changes.
        image.data[image.LOOP + 50] ^= 1
    if pdata:
        last = image.FIRST_INITIALIZER + count * 32
        with_pdata(image, [
            (image.FIRST_INITIALIZER, max(last, image.FIRST_INITIALIZER + 1)),
            (image.LOOP, image.LOOP + 0x80),
            (image.HELPER, image.HELPER + len(runtime.HDR_OUTPUT_HELPER_BYTES)),
            (image.FACTORY, image.FACTORY + 1),
        ])
    return image


class ResilientLayoutTests(unittest.TestCase):
    def plan(self, image, name="msedge.dll"):
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / name
            dll.write_bytes(image.data)
            result = runtime.make_runtime_plan(dll)
            self.assertEqual(dll.read_bytes(), image.data, "DLL on disk must stay unchanged")
            return result

    def assert_checks_do_not_overlap_writes(self, plan, image):
        for label, rva, expected in plan.checks:
            self.assertEqual(bytes(image.data[rva:rva + len(expected)]), expected, label)
            for item in plan.writes:
                self.assertTrue(
                    item.rva + len(item.patched) <= rva or rva + len(expected) <= item.rva,
                    f"{item.label} overlaps {label}",
                )
        for item in plan.writes:
            self.assertEqual(bytes(image.data[item.rva:item.rva + len(item.original)]), item.original)

    def test_verified_layouts_still_use_strict_analyzers(self):
        plan = self.plan(edge_image())
        self.assertEqual(plan.layout["strategy_summary"], "gamma=strict, output=strict")
        self.assertEqual(plan.layout["fallback_reasons"], [])

    def test_constructor_count_drift_uses_paired_load_fallback(self):
        for count in (96, 121, 130):
            with self.subTest(count=count):
                image = edge_image(count)
                plan = self.plan(image)
                self.assertEqual(plan.layout["gamma_strategy"], "fallback")
                self.assertEqual(plan.layout["output_strategy"], "strict")
                gamma = [item for item in plan.writes if item.label.startswith("sRGB initializer ")]
                self.assertEqual(len(gamma), count)
                for item in gamma:
                    target = item.rva + 7 + struct.unpack_from("<i", item.patched, 3)[0]
                    self.assertEqual(item.patched[:3], b"\x48\x8D\x0D")
                    self.assertEqual(target, image.GAMMA22)
                self.assert_checks_do_not_overlap_writes(plan, image)
                self.assertIn("Unexpected Edge singleton constructors", " ".join(plan.layout["fallback_reasons"]))

    def test_extra_non_bt709_loads_are_left_untouched(self):
        # Electron 45 (Chromium codegen) has 53 constructors plus 3 Skia loads
        # of the same constant. Strict Edge analysis rejects that; the
        # fallback redirects only the BT.709 pairs.
        image = edge_image(100)
        other_gamut = 0x6400  # e.g. Display-P3 primaries sharing the sRGB curve
        image.put(other_gamut, bytes(range(36)))
        p3_site = image.FIRST_INITIALIZER + 5 * 32
        image.put(p3_site + 10, runtime.rel32(p3_site + 7, 7, other_gamut))
        plan = self.plan(image)
        self.assertEqual(plan.layout["gamma_strategy"], "fallback")
        self.assertEqual(plan.layout["gamma_unpaired_rvas"], [p3_site])
        redirected = {item.rva for item in plan.writes if item.label.startswith("sRGB initializer ")}
        self.assertNotIn(p3_site, redirected)
        self.assertEqual(len(redirected), 99)

    def test_movups_pairs_are_redirected_with_their_offsets(self):
        image = edge_image(0)
        for index in range(20):
            base = 0x1100 + index * 40
            image.put(base, b"\x0F\x10\x05" + runtime.rel32(base, 7, image.SRGB))
            image.put(base + 7, b"\x0F\x10\x0D" + runtime.rel32(base + 7, 7, image.SRGB + 12))
            image.put(base + 14, b"\x0F\x10\x15" + runtime.rel32(base + 14, 7, image.GAMUT))
        plan = self.plan(image)
        gamma = [item for item in plan.writes if item.label.startswith("sRGB initializer ")]
        self.assertEqual(len(gamma), 40)
        for item in gamma:
            target = item.rva + 7 + struct.unpack_from("<i", item.patched, 3)[0]
            expected = image.GAMMA22 + (12 if item.label.endswith("b") else 0)
            self.assertEqual(target, expected, item.label)
            self.assertEqual(item.patched[:3], item.original[:3])

    def test_movups_pairs_in_either_order_and_partial_copies(self):
        image = edge_image(0)
        reversed_sites = []
        partial_sites = []
        for index in range(24):
            base = 0x1100 + index * 40
            if index < 4:  # only the first 16 bytes are copied: never rewrite
                image.put(base, b"\x0F\x10\x05" + runtime.rel32(base, 7, image.SRGB))
                image.put(base + 7, b"\x0F\x10\x15" + runtime.rel32(base + 7, 7, image.GAMUT))
                partial_sites.append(base)
                continue
            image.put(base, b"\x0F\x10\x0D" + runtime.rel32(base, 7, image.SRGB + 12))
            image.put(base + 7, b"\x0F\x10\x05" + runtime.rel32(base + 7, 7, image.SRGB))
            image.put(base + 14, b"\x0F\x10\x15" + runtime.rel32(base + 14, 7, image.GAMUT))
            reversed_sites.append(base)
        plan = self.plan(image)
        self.assertEqual(plan.layout["gamma_unpaired_rvas"], partial_sites)
        redirected = {item.rva: item for item in plan.writes if item.label.startswith("sRGB initializer ")}
        self.assertEqual(len(redirected), 2 * len(reversed_sites))
        for base in reversed_sites:
            for rva, expected in ((base, image.GAMMA22 + 12), (base + 7, image.GAMMA22)):
                item = redirected[rva]
                self.assertEqual(rva + 7 + struct.unpack_from("<i", item.patched, 3)[0], expected)
        for base in partial_sites:
            self.assertNotIn(base, redirected)

    def test_bt709_pair_must_be_the_next_data_load(self):
        image = edge_image(130)
        other_gamut = 0x6400
        image.put(other_gamut, bytes(range(36)))
        sites = [image.FIRST_INITIALIZER + index * 32 for index in range(0, 20, 2)]
        for base in sites:  # a P3 load sits between the sRGB and BT.709 loads
            image.put(base + 7, b"\x48\x8D\x15" + runtime.rel32(base + 7, 7, other_gamut))
            image.put(base + 14, b"\x48\x8D\x05" + runtime.rel32(base + 14, 7, image.GAMUT))
        plan = self.plan(image)
        self.assertEqual(plan.layout["gamma_unpaired_rvas"], sites)
        redirected = {item.rva for item in plan.writes if item.label.startswith("sRGB initializer ")}
        self.assertTrue(redirected.isdisjoint(sites))
        self.assertEqual(len(redirected), 120)

    def test_too_many_other_loads_reject_the_constant(self):
        image = edge_image(100)
        other_gamut = 0x6400
        image.put(other_gamut, bytes(range(36)))
        for index in range(20):
            base = image.FIRST_INITIALIZER + index * 32
            image.put(base + 10, runtime.rel32(base + 7, 7, other_gamut))
        with self.assertRaisesRegex(runtime.PatchError, "GPU gamma.*other load"):
            self.plan(image)

    def test_partial_gamut_load_is_not_a_pair(self):
        image = edge_image(130)
        base = image.FIRST_INITIALIZER + 7
        image.put(base + 3, runtime.rel32(base, 7, image.GAMUT + 4))
        plan = self.plan(image)
        self.assertEqual(plan.layout["gamma_unpaired_rvas"], [image.FIRST_INITIALIZER])

    def test_too_few_constant_loads_are_rejected(self):
        with self.assertRaisesRegex(runtime.PatchError, "GPU gamma"):
            self.plan(edge_image(3))

    def test_changed_output_loop_uses_helper_detour(self):
        image = edge_image(broken_loop=True)
        plan = self.plan(image)
        self.assertEqual(plan.layout["gamma_strategy"], "strict")
        self.assertEqual(plan.layout["output_strategy"], "fallback")
        stubs = [item for item in plan.writes if item.label.startswith(resilient.LABEL_DETOUR_STUB)]
        hook = plan.writes[-1]
        self.assertTrue(stubs)
        self.assertEqual(plan.writes[-1 - len(stubs):-1], stubs, "stub chunks precede the hook")
        self.assertEqual(hook.label, resilient.LABEL_DETOUR_HOOK)
        self.assertEqual(hook.rva, image.HELPER)
        self.assertEqual(hook.original, bytes.fromhex("56 57 48 89 C8"))
        self.assertEqual(hook.patched[:1], b"\xE9")
        target = hook.rva + 5 + struct.unpack_from("<i", hook.patched, 1)[0]
        self.assertEqual(target, stubs[0].rva)
        for stub in stubs:
            self.assertEqual(stub.original, b"\xCC" * len(stub.original))
            self.assertFalse(image.HELPER <= stub.rva < image.HELPER + len(runtime.HDR_OUTPUT_HELPER_BYTES))
        self.assert_checks_do_not_overlap_writes(plan, image)
        self.assertTrue(any(label == "ScreenWin output helper body" for label, _r, _e in plan.checks))

    def test_detour_chunks_link_and_resume_correctly(self):
        helper = 0x5000
        caves = [(0x1000, 14), (0x2000, 16), (0x3000, 11), (0x4000, 40)]
        chunks = resilient.build_output_detour_chunks(caves, helper)
        self.assertGreater(len(chunks), 1)
        for (rva, data), (cave_rva, capacity) in zip(chunks, caves):
            self.assertEqual(rva, cave_rva)
            self.assertLessEqual(len(data), capacity)
        for index, (rva, data) in enumerate(chunks[:-1]):
            self.assertEqual(data[-5], 0xE9)
            link = rva + len(data) + struct.unpack_from("<i", data, len(data) - 4)[0]
            self.assertEqual(link, chunks[index + 1][0])
        rva, data = chunks[-1]
        resume = rva + len(data) + struct.unpack_from("<i", data, len(data) - 4)[0]
        self.assertEqual(resume, helper + 5)
        self.assertEqual(data[-10:-5], bytes.fromhex("56 57 48 89 C8"))
        with self.assertRaisesRegex(runtime.PatchError, "too small"):
            resilient.build_output_detour_chunks([(0x1000, 10)] * 3, helper)

    def test_padding_caves_exclude_undeclared_code(self):
        image = edge_image(broken_loop=True)
        # Undeclared code between two declared functions: the gap is no
        # longer pure INT3 padding and must not be used.
        gap_start = image.LOOP + 0x80
        image.put(gap_start + 0x40, b"\x55\x48\x89\xE5\xC3")
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "msedge.dll"
            dll.write_bytes(image.data)
            sections = runtime.read_pe_sections(dll)
            text, text_rva = runtime.read_text_section(dll, sections)
            caves = resilient.find_padding_caves(dll, sections, text, text_rva)
        self.assertTrue(caves)
        for rva, capacity in caves:
            self.assertFalse(rva <= gap_start + 0x40 < rva + capacity)
            self.assertEqual(bytes(image.data[rva - 1:rva + capacity + 1]), b"\xCC" * (capacity + 2))

    def test_both_components_can_fall_back_together(self):
        image = edge_image(125, broken_loop=True)
        plan = self.plan(image)
        self.assertEqual(plan.layout["strategy_summary"], "gamma=fallback, output=fallback")
        labels = [item.label for item in plan.writes]
        gamma = [label for label in labels if label.startswith("sRGB initializer ")]
        self.assertEqual(labels[:len(gamma)], gamma)
        self.assertEqual(len(gamma), 125)
        self.assertTrue(all(label.startswith(resilient.LABEL_DETOUR_STUB) for label in labels[len(gamma):-1]))
        self.assertEqual(labels[-1], resilient.LABEL_DETOUR_HOOK)
        self.assert_checks_do_not_overlap_writes(plan, image)
        self.assertIn("output=fallback", runtime.describe_plan(plan))

    def test_detour_requires_a_safe_code_cave(self):
        with self.assertRaisesRegex(runtime.PatchError, "SDR output path.*(code cave|function boundaries)"):
            self.plan(edge_image(broken_loop=True, pdata=False))

    def test_detour_requires_the_exact_helper_and_a_caller(self):
        image = edge_image(broken_loop=True)
        image.data[image.HELPER + 20] ^= 1
        with self.assertRaisesRegex(runtime.PatchError, "one exact output helper"):
            self.plan(image)
        image = edge_image(broken_loop=True)
        image.put(image.LOOP, b"\xCC" * 0x60)
        with self.assertRaisesRegex(runtime.PatchError, "0 direct output-helper reference"):
            self.plan(image)

    def test_errors_name_every_rejected_strategy(self):
        image = edge_image(3, broken_loop=True, pdata=False)
        with self.assertRaises(runtime.PatchError) as context:
            self.plan(image)
        message = str(context.exception)
        for fragment in ("GPU gamma", "strict:", "fallback:", "SDR output path"):
            self.assertIn(fragment, message)

    def test_fallback_writes_are_routed_to_their_process_roles(self):
        image = edge_image(125, broken_loop=True)
        plan = self.plan(image)
        base = 0x10_0000
        memory = {base + item.rva: item.original for item in plan.writes}
        order = []

        def read(_process, address, size):
            return memory[address][:size]

        def write(_process, address, data):
            order.append(address - base)
            memory[address] = data

        flush = mock.Mock(return_value=1)
        with mock.patch.object(hot, "read_memory", side_effect=read), \
                mock.patch.object(hot, "write_memory", side_effect=write), \
                mock.patch.object(hot.kernel32, "FlushInstructionCache", flush):
            hot.reconcile_module_for_role(None, 1, base, plan, "gpu")
            first = plan.writes[0]
            self.assertEqual(memory[base + first.rva], first.patched)
            self.assertEqual(memory[base + image.HELPER], b"\x56\x57\x48\x89\xC8")
            for item in plan.writes:
                memory[base + item.rva] = item.original
            order.clear()
            hot.reconcile_module_for_role(None, 2, base, plan, "browser")
            output = [item for item in plan.writes if item.label.startswith("ScreenWin output detour")]
            self.assertEqual(order, [item.rva for item in output], "stub must exist before the jump")
            self.assertEqual(memory[base + first.rva], first.original)
            order.clear()
            hot.reconcile_module_for_role(None, 2, base, plan, "browser", enabled=False)
            self.assertEqual(order, [item.rva for item in reversed(output)], "jump must be removed first")


@unittest.skipUnless(sys.platform == "win32", "executes x64 code with the Win64 ABI")
class DetourExecutionTests(unittest.TestCase):
    """Run the real helper bytes with and without the detour (Windows only)."""

    def setUp(self):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.VirtualAlloc.restype = ctypes.c_void_p
        kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
        kernel32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
        self.kernel32 = kernel32

    def run_helper(self, hooked):
        page = self.kernel32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
        self.assertTrue(page)
        try:
            blob = bytearray(b"\xCC" * 0x200)
            helper = runtime.HDR_OUTPUT_HELPER_BYTES
            blob[:len(helper)] = helper
            chunks = resilient.build_output_detour_chunks(
                [(0x100, 14), (0x130, 16), (0x160, 11), (0x180, 40)], 0
            )
            for rva, data in chunks:
                blob[rva:rva + len(data)] = data
            if hooked:
                blob[:5] = b"\xE9" + runtime.rel32(0, 5, chunks[0][0])
            ctypes.memmove(page, bytes(blob), len(blob))
            function = ctypes.CFUNCTYPE(
                None, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64,
                ctypes.c_void_p, ctypes.c_uint64,
            )(page)
            results = {}
            for usage in range(3):
                for alpha in range(2):
                    state = (ctypes.c_ubyte * 0x1C8)(*([0xEE] * 0x1C8))
                    color = (ctypes.c_ubyte * 68)(*[(index * 3 + usage * 7 + alpha) & 0xFF for index in range(68)])
                    fmt = 0x1122334455667700 | (usage << 4) | alpha
                    function(ctypes.addressof(state), 0xABCDEF00 | usage, 0x12345600 | alpha,
                             ctypes.addressof(color), fmt)
                    raw = bytes(state)
                    slots = set()
                    for slot in range(6):
                        if (raw[slot * 68:(slot + 1) * 68] == bytes(color)
                                and struct.unpack_from("<Q", raw, 0x198 + slot * 8)[0] == fmt):
                            slots.add(slot)
                        else:
                            self.assertEqual(raw[slot * 68:(slot + 1) * 68], b"\xEE" * 68)
                    results[usage, alpha] = slots
            return results
        finally:
            self.kernel32.VirtualFree(ctypes.c_void_p(page), 0, 0x8000)

    def test_original_helper_semantics(self):
        results = self.run_helper(False)
        for (usage, alpha), slots in results.items():
            self.assertEqual(slots, {usage * 2 + alpha})

    def test_detour_mirrors_only_wide_color_gamut_into_srgb(self):
        results = self.run_helper(True)
        for (usage, alpha), slots in results.items():
            expected = {usage * 2 + alpha} | ({alpha} if usage == 1 else set())
            self.assertEqual(slots, expected)


if __name__ == "__main__":
    unittest.main()
