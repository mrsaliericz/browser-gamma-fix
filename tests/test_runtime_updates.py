import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import hot_attach_gamma22 as hot
import tray_gamma22 as tray
from tray_gamma22 import BrowserGenerations


class RuntimeUpdateTests(unittest.TestCase):
    def test_vivaldi_standard_locations(self):
        with mock.patch.dict(tray.os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}):
            locations = dict(tray.supported_browser_locations())["Vivaldi"]
        self.assertEqual(len(locations), 3)
        self.assertTrue(all(p.name == "vivaldi.exe" for p in locations))
        self.assertIn(Path(r"C:\Users\Test\AppData\Local\Vivaldi\Application\vivaldi.exe"), locations)

    def test_vivaldi_version_discovery_and_generation_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = root / "vivaldi.exe"
            browser.touch()
            for version in ("8.2.4133.9", "8.2.4133.52"):
                (root / version).mkdir()
                (root / version / "vivaldi.dll").write_bytes(b"compatible")
            dll = hot.locate_chrome_dll(browser, None)
            self.assertEqual(dll.parent.name, "8.2.4133.52")
            generations = BrowserGenerations(browser, planner=self.fake_plan)
            generations.activate(dll)
            wrong = dll.with_name("chrome.dll")
            wrong.write_bytes(b"compatible")
            with self.assertRaises(hot.PatchError):
                generations.activate(wrong)
            with tempfile.TemporaryDirectory() as other:
                outside = Path(other) / "vivaldi.dll"
                outside.write_bytes(b"compatible")
                with self.assertRaises(hot.PatchError):
                    generations._validated_identity(outside)

    def make_installation(self, root: Path):
        application = root / "Application"
        application.mkdir()
        browser = application / "msedge.exe"
        browser.write_bytes(b"test browser")
        first = application / "151.0.1.1" / "msedge.dll"
        second = application / "151.0.1.2" / "msedge.dll"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"first compatible generation")
        second.write_bytes(b"second compatible generation")
        return browser, first, second

    @staticmethod
    def fake_plan(dll: Path):
        data = dll.read_bytes()
        if data.startswith(b"unsupported"):
            raise hot.PatchError("unknown structural layout")
        return SimpleNamespace(
            dll=dll,
            dll_hash=hashlib.sha256(data).hexdigest().upper(),
            checks=[],
            writes=[],
        )

    def test_new_version_is_activated_without_dropping_old_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            browser, first, second = self.make_installation(Path(directory))
            selected = [first]
            registry = BrowserGenerations(
                browser,
                locator=lambda _browser, _explicit: selected[0],
                planner=self.fake_plan,
                clock=lambda: 100.0,
            )
            registry.activate(first)
            selected[0] = second

            update = registry.poll(force=True)

            self.assertEqual(update[0], "updated")
            self.assertEqual(registry.active_dll, second.resolve())
            self.assertEqual(len(registry.plans_by_dll), 2)
            self.assertIn(hot.normalized_path(first.resolve()), registry.plans_by_dll)
            self.assertIn(hot.normalized_path(second.resolve()), registry.plans_by_dll)

    def test_in_place_replacement_keeps_both_memory_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            browser, first, _second = self.make_installation(Path(directory))
            registry = BrowserGenerations(
                browser,
                locator=lambda _browser, _explicit: first,
                planner=self.fake_plan,
                clock=lambda: 100.0,
            )
            registry.activate(first)
            first.write_bytes(b"replacement generation with a different size")

            update = registry.poll(force=True)

            key = hot.normalized_path(first.resolve())
            self.assertEqual(update[0], "updated")
            self.assertEqual(len(registry.plans_by_dll[key]), 2)

    def test_unsupported_update_preserves_last_verified_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            browser, first, second = self.make_installation(Path(directory))
            registry = BrowserGenerations(
                browser,
                locator=lambda _browser, _explicit: second,
                planner=self.fake_plan,
                clock=lambda: 100.0,
            )
            registry.activate(first)
            second.write_bytes(b"unsupported new layout")

            update = registry.poll(force=True)

            self.assertEqual(update[0], "error")
            self.assertIn("unknown structural layout", update[1])
            self.assertEqual(registry.active_dll, first.resolve())
            self.assertEqual(len(registry.plans_by_dll), 1)

    def test_module_match_accepts_original_or_patched_writes(self):
        write = SimpleNamespace(
            rva=0x30,
            original=b"original",
            patched=b"patched!",
        )
        plan = SimpleNamespace(
            checks=[("signature", 0x10, b"check")],
            writes=[write],
        )
        base = 0x1000
        memory = {
            base + 0x10: b"check",
            base + 0x30: b"patched!",
        }

        with mock.patch.object(
            hot,
            "read_memory",
            side_effect=lambda _process, address, _size: memory[address],
        ):
            self.assertTrue(hot.module_matches_plan(object(), base, plan))
            memory[base + 0x30] = b"unknown!"
            self.assertFalse(hot.module_matches_plan(object(), base, plan))

    def test_in_memory_fallback_selects_verified_loaded_image(self):
        first = SimpleNamespace(dll=Path("first.dll"))
        second = SimpleNamespace(dll=Path("second.dll"))
        plans = {"first": [first], "second": [second]}

        with mock.patch.object(
            hot, "loaded_image_bases", return_value=[0x1000, 0x2000]
        ), mock.patch.object(
            hot,
            "module_matches_plan",
            side_effect=lambda _process, base, plan: base == 0x2000 and plan is second,
        ):
            match = hot.find_matching_loaded_plan(object(), plans)

        self.assertEqual(match, (0x2000, second))


class TrayInterfaceTests(unittest.TestCase):
    def test_about_metadata_is_present(self):
        self.assertEqual(tray.APP_NAME, "Browser Gamma Fix")
        self.assertEqual(tray.APP_VERSION, "0.7.0")
        self.assertEqual(tray.APP_AUTHOR, "Jaroslav Safar")
        self.assertEqual(tray.APP_EMAIL, "hello@jaroslavsafar.com")

    def test_enable_autostart_writes_current_executable_to_hkcu(self):
        key = object()
        context = mock.MagicMock()
        context.__enter__.return_value = key
        with mock.patch.object(
            tray, "autostart_command", return_value='"C:\\Tools\\Gamma22Tray.exe"'
        ), mock.patch.object(
            tray.winreg, "CreateKeyEx", return_value=context
        ) as create_key, mock.patch.object(tray.winreg, "SetValueEx") as set_value:
            tray.set_autostart(True)

        create_key.assert_called_once_with(
            tray.winreg.HKEY_CURRENT_USER,
            tray.RUN_KEY,
            0,
            tray.winreg.KEY_SET_VALUE,
        )
        set_value.assert_called_once_with(
            key,
            tray.RUN_VALUE_NAME,
            0,
            tray.winreg.REG_SZ,
            '"C:\\Tools\\Gamma22Tray.exe"',
        )

    def test_disable_autostart_removes_only_its_own_run_value(self):
        key = object()
        context = mock.MagicMock()
        context.__enter__.return_value = key
        with mock.patch.object(
            tray, "autostart_command", return_value='"C:\\Tools\\Gamma22Tray.exe"'
        ), mock.patch.object(
            tray.winreg, "OpenKey", return_value=context
        ), mock.patch.object(tray.winreg, "DeleteValue") as delete_value:
            tray.set_autostart(False)

        delete_value.assert_called_once_with(key, tray.RUN_VALUE_NAME)

    def test_update_restart_waits_for_settling_period(self):
        now = [100.0]
        coordinator = tray.UpdateRestartCoordinator(
            clock=lambda: now[0], settle_seconds=15.0
        )

        coordinator.schedule("Chrome: old -> new")
        self.assertTrue(coordinator.pending)
        self.assertFalse(coordinator.due())
        now[0] = 114.9
        self.assertFalse(coordinator.due())
        now[0] = 115.0
        self.assertTrue(coordinator.due())

    def test_second_update_resets_settling_deadline(self):
        now = [100.0]
        coordinator = tray.UpdateRestartCoordinator(
            clock=lambda: now[0], settle_seconds=15.0
        )

        coordinator.schedule("Chrome: old -> intermediate")
        now[0] = 110.0
        coordinator.schedule("Chrome: intermediate -> current")
        now[0] = 115.0
        self.assertFalse(coordinator.due())
        now[0] = 125.0
        self.assertTrue(coordinator.due())

    def test_frozen_restart_command_waits_for_current_pid(self):
        with mock.patch.object(tray.sys, "frozen", True, create=True), mock.patch.object(
            tray.sys, "executable", r"C:\Tools\Gamma22Tray.exe"
        ):
            command = tray.restart_command(1234)

        self.assertEqual(
            command,
            [
                str(Path(r"C:\Tools\Gamma22Tray.exe").resolve()),
                tray.RESTART_WAIT_ARGUMENT,
                "1234",
            ],
        )

    def test_attach_retry_limit_prevents_debugger_attach_storm(self):
        self.assertFalse(
            tray.attach_attempt_is_final(1, unsupported_observed=False)
        )
        self.assertFalse(
            tray.attach_attempt_is_final(2, unsupported_observed=False)
        )
        self.assertTrue(
            tray.attach_attempt_is_final(3, unsupported_observed=False)
        )
        self.assertTrue(
            tray.attach_attempt_is_final(1, unsupported_observed=True)
        )

    def test_active_status_requires_verified_browser_and_gpu(self):
        roles = {10: "browser", 20: "gpu"}
        common = {
            "enabled": True,
            "current": {10, 20},
            "roles": roles,
            "active_error": None,
            "update_pending": False,
        }

        self.assertEqual(
            tray.browser_runtime_status(
                **common, verified=set(), completed={10, 20}
            ),
            "not attached",
        )
        self.assertEqual(
            tray.browser_runtime_status(
                **common, verified={10}, completed={10, 20}
            ),
            "partially attached",
        )
        self.assertEqual(
            tray.browser_runtime_status(
                **common, verified={10, 20}, completed={10, 20}
            ),
            "active",
        )

    def test_unrelated_edge_webview_dll_is_not_a_trusted_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            edge_root = root / "Microsoft" / "Edge" / "Application"
            webview_root = root / "Microsoft" / "EdgeWebView" / "Application"
            current = edge_root / "151.0.1.1" / "msedge.dll"
            updated = edge_root / "151.0.1.2" / "msedge.dll"
            unrelated = webview_root / "151.0.1.2" / "msedge.dll"
            for dll in (current, updated, unrelated):
                dll.parent.mkdir(parents=True, exist_ok=True)
                dll.write_bytes(dll.parent.name.encode("ascii"))
            plan = SimpleNamespace(dll=current.resolve())
            plans = {hot.normalized_path(current.resolve()): [plan]}

            self.assertTrue(hot.observed_dll_is_trusted(updated, plans))
            self.assertFalse(hot.observed_dll_is_trusted(unrelated, plans))


if __name__ == "__main__":
    unittest.main()
