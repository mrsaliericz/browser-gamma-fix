#!/usr/bin/env python3
"""Native Win32 notification-area frontend for Gamma22 hot attach."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import winreg

import hot_attach_gamma22 as hot


WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_CONTEXTMENU = 0x007B
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_STATUS_CHANGED = WM_APP + 2
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4
NOTIFYICON_VERSION_4 = 4
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
MF_STRING = 0
MF_GRAYED = 0x01
MF_CHECKED = 0x08
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
SW_SHOWNORMAL = 1
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
CMD_STATUS = 100
CMD_LOG = 101
CMD_EXIT = 102
CMD_TOGGLE = 103
CMD_AUTOSTART = 104
CMD_ABOUT = 105
UPDATE_POLL_SECONDS = 5.0
FAILED_GENERATION_RETRY_SECONDS = 30.0
UPDATE_RESTART_SETTLE_SECONDS = 15.0
RESTART_WAIT_ARGUMENT = "--gamma22-restart-after-pid"
RESTART_PARENT_TIMEOUT_MS = 60_000
MAX_ATTACH_ATTEMPTS = 3
APP_NAME = "Gamma22Tray"
APP_VERSION = "0.6.0"
APP_AUTHOR = "Jaroslav Safar"
APP_EMAIL = "hello@jaroslavsafar.com"
APP_URL = "https://github.com/mrsaliericz/chromium-hdr-sdr-gamma22"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "Gamma22Tray"

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE,
    wintypes.LPCWSTR,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_size_t,
    wintypes.LPCWSTR,
]
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.MessageBoxW.argtypes = [
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.UINT,
]
user32.MessageBoxW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.ExtractIconExW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HICON),
    ctypes.POINTER(wintypes.HICON),
    wintypes.UINT,
]
shell32.ExtractIconExW.restype = wintypes.UINT
shell32.ShellExecuteW.argtypes = [
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_int,
]

stop_event = threading.Event()
status_lock = threading.Lock()
fix_lock = threading.Lock()
current_status = "Starting"
fix_enabled = True
switch_in_progress = False
window_handle = None
notify_data = None
active_icon = None
inactive_icon = None
owned_icons: set[int] = set()
log_path = None


class BrowserGenerations:
    """Cache verified runtime plans across browser update transitions."""

    def __init__(self, browser: Path, *, locator=None, planner=None, clock=None):
        self.browser = browser.resolve()
        self._locator = locator or hot.locate_chrome_dll
        self._planner = planner or hot.make_runtime_plan
        self._clock = clock or time.monotonic
        self.plans_by_dll: dict[str, list] = {}
        self._plans_by_identity: dict[tuple[str, int, int], object] = {}
        self._failed_identities: dict[tuple[str, int, int], tuple[float, str]] = {}
        self.active_identity: tuple[str, int, int] | None = None
        self.active_dll: Path | None = None
        self.active_error: str | None = None
        self.next_poll = 0.0

    def _validated_identity(self, dll: Path) -> tuple[Path, tuple[str, int, int]]:
        resolved = dll.resolve(strict=True)
        expected_name = "msedge.dll" if self.browser.name.lower() == "msedge.exe" else "chrome.dll"
        if resolved.name.lower() != expected_name:
            raise hot.PatchError(f"Unexpected browser DLL name: {resolved}")
        try:
            resolved.relative_to(self.browser.parent.resolve())
        except ValueError as error:
            raise hot.PatchError(
                f"Refusing browser DLL outside {self.browser.parent}: {resolved}"
            ) from error
        stat = resolved.stat()
        return resolved, (
            hot.normalized_path(resolved),
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )

    def register(self, dll: Path):
        resolved, identity = self._validated_identity(dll)
        existing = self._plans_by_identity.get(identity)
        if existing is not None:
            return existing, False

        now = self._clock()
        failed = self._failed_identities.get(identity)
        if failed is not None and now < failed[0]:
            raise hot.PatchError(failed[1])
        try:
            plan = self._planner(resolved)
        except Exception as error:
            message = str(error)
            self._failed_identities[identity] = (
                now + FAILED_GENERATION_RETRY_SECONDS,
                message,
            )
            raise

        self._failed_identities.pop(identity, None)
        self._plans_by_identity[identity] = plan
        key = identity[0]
        self.plans_by_dll.setdefault(key, []).append(plan)
        return plan, True

    def activate(self, dll: Path):
        resolved, identity = self._validated_identity(dll)
        plan, added = self.register(resolved)
        changed = identity != self.active_identity
        self.active_identity = identity
        self.active_dll = resolved
        self.active_error = None
        return plan, added, changed

    def poll(self, *, force: bool = False) -> tuple[str, str] | None:
        now = self._clock()
        if not force and now < self.next_poll:
            return None
        self.next_poll = now + UPDATE_POLL_SECONDS
        try:
            candidate = self._locator(self.browser, None)
            _resolved, identity = self._validated_identity(candidate)
            if identity == self.active_identity:
                self.active_error = None
                return None
            previous = self.active_dll
            plan, added, _changed = self.activate(candidate)
        except Exception as error:
            self.active_error = str(error)
            return "error", self.active_error

        old_version = previous.parent.name if previous is not None else "none"
        new_version = self.active_dll.parent.name if self.active_dll is not None else "unknown"
        action = "analyzed" if added else "selected cached"
        return (
            "updated",
            f"{old_version} -> {new_version}; {action} generation {plan.dll_hash[:12]}",
        )

    def register_observed(self, dll: Path):
        """Analyze a DLL observed in a live process during a mixed update."""
        return self.register(dll)


class UpdateRestartCoordinator:
    """Suspend runtime writes and restart after a browser update settles."""

    def __init__(self, *, clock=None, settle_seconds=UPDATE_RESTART_SETTLE_SECONDS):
        self._clock = clock or time.monotonic
        self.settle_seconds = settle_seconds
        self.deadline: float | None = None
        self.details: list[str] = []

    @property
    def pending(self) -> bool:
        return self.deadline is not None

    def schedule(self, detail: str) -> None:
        self.details.append(detail)
        self.deadline = self._clock() + self.settle_seconds

    def due(self) -> bool:
        return self.deadline is not None and self._clock() >= self.deadline


def restart_command(parent_pid: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [
            str(Path(sys.executable).resolve()),
            RESTART_WAIT_ARGUMENT,
            str(parent_pid),
        ]
    return [
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve()),
        RESTART_WAIT_ARGUMENT,
        str(parent_pid),
    ]


def wait_for_restart_parent(arguments: list[str]) -> bool:
    """Wait for the old tray instance before acquiring the single-instance mutex."""
    if RESTART_WAIT_ARGUMENT not in arguments:
        return True
    index = arguments.index(RESTART_WAIT_ARGUMENT)
    try:
        parent_pid = int(arguments[index + 1])
    except (IndexError, ValueError):
        return False
    synchronize = 0x00100000
    wait_object_0 = 0
    process = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not process:
        return True
    try:
        return (
            kernel32.WaitForSingleObject(process, RESTART_PARENT_TIMEOUT_MS)
            == wait_object_0
        )
    finally:
        hot.close_handle(process)


def request_process_restart() -> None:
    command = restart_command(os.getpid())
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    subprocess.Popen(
        command,
        close_fds=True,
        creationflags=creationflags,
        cwd=str(Path(sys.executable).resolve().parent),
    )
    stop_event.set()
    if window_handle:
        user32.PostMessageW(window_handle, WM_CLOSE, 0, 0)


def attach_attempt_is_final(attempts: int, *, unsupported_observed: bool) -> bool:
    return unsupported_observed or attempts >= MAX_ATTACH_ATTEMPTS


def browser_runtime_status(
    *,
    enabled: bool,
    current: set[int],
    roles: dict[int, str],
    verified: set[int],
    completed: set[int],
    active_error: str | None,
    update_pending: bool,
) -> str:
    if update_pending:
        return "update settling/restart pending"
    if not enabled:
        return "off"
    if active_error is not None:
        return "unsupported update"
    required = {
        pid for pid in current if roles.get(pid) in {"browser", "gpu"}
    }
    if not required:
        return "waiting" if not current else "detecting processes"
    verified_current = required & verified
    verified_roles = {roles[pid] for pid in verified_current}
    unverified = required - verified_current
    if not unverified and {"browser", "gpu"}.issubset(verified_roles):
        return "active"
    if unverified & completed:
        return "partially attached" if verified_current else "not attached"
    return "applying"


def set_status(value: str) -> None:
    global current_status
    with status_lock:
        current_status = value
    if window_handle:
        user32.PostMessageW(window_handle, WM_STATUS_CHANGED, 0, 0)


def status_text() -> str:
    with status_lock:
        return current_status


def fix_mode() -> tuple[bool, bool]:
    with fix_lock:
        return fix_enabled, switch_in_progress


def request_fix_toggle() -> None:
    global fix_enabled, switch_in_progress
    with fix_lock:
        if switch_in_progress:
            return
        fix_enabled = not fix_enabled
        switch_in_progress = True
        destination = "ON" if fix_enabled else "OFF"
    set_status(f"Switching Gamma 2.2 fix {destination}…")


def finish_fix_toggle() -> None:
    global switch_in_progress
    with fix_lock:
        switch_in_progress = False


def update_tray_state() -> None:
    if notify_data is None:
        return
    enabled, _switching = fix_mode()
    notify_data.uFlags = NIF_ICON | NIF_TIP
    notify_data.hIcon = active_icon if enabled else inactive_icon
    notify_data.szTip = f"Chromium Gamma 2.2 — {status_text()}"[:127]
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(notify_data))


def open_log() -> None:
    if log_path is not None:
        shell32.ShellExecuteW(None, "open", str(log_path), None, None, SW_SHOWNORMAL)


def autostart_command() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    return f'"{Path(sys.executable).resolve()}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool) -> None:
    command = autostart_command()
    if command is None:
        raise RuntimeError("Start with Windows is available only in the packaged EXE")
    if enabled:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)
        return
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
    except FileNotFoundError:
        pass


def toggle_autostart() -> None:
    try:
        enabled = not autostart_enabled()
        set_autostart(enabled)
        set_status(f"Start with Windows {'enabled' if enabled else 'disabled'}")
    except Exception as error:
        user32.MessageBoxW(
            window_handle,
            f"Could not change Start with Windows:\n\n{error}",
            APP_NAME,
            0x00000010,
        )


def show_about() -> None:
    user32.MessageBoxW(
        window_handle,
        (
            f"{APP_NAME}\n"
            f"Version {APP_VERSION}\n\n"
            "Windows HDR SDR gamma 2.2 runtime fix for Google Chrome, "
            "Microsoft Edge and Brave.\n\n"
            f"Author: {APP_AUTHOR}\n"
            f"Contact: {APP_EMAIL}\n\n"
            f"{APP_URL}\n\n"
            "Free and open-source software."
        ),
        f"About {APP_NAME}",
        0x00000040,
    )


def show_menu(hwnd) -> None:
    menu = user32.CreatePopupMenu()
    if not menu:
        return
    try:
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, CMD_STATUS, status_text()[:80])
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        enabled, switching = fix_mode()
        toggle_label = (
            "Switching…"
            if switching
            else "Disable Gamma 2.2 fix"
            if enabled
            else "Enable Gamma 2.2 fix"
        )
        toggle_flags = MF_STRING | (MF_GRAYED if switching else 0)
        user32.AppendMenuW(menu, toggle_flags, CMD_TOGGLE, toggle_label)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        autostart_flags = MF_STRING
        if autostart_enabled():
            autostart_flags |= MF_CHECKED
        if autostart_command() is None:
            autostart_flags |= MF_GRAYED
        user32.AppendMenuW(
            menu, autostart_flags, CMD_AUTOSTART, "Start with Windows"
        )
        user32.AppendMenuW(menu, MF_STRING, CMD_ABOUT, f"About {APP_NAME}…")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_LOG, "Open diagnostic log")
        user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "Exit")
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(hwnd)
        command = user32.TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | TPM_RETURNCMD,
            point.x,
            point.y,
            0,
            hwnd,
            None,
        )
        if command == CMD_TOGGLE:
            request_fix_toggle()
        elif command == CMD_AUTOSTART:
            toggle_autostart()
        elif command == CMD_ABOUT:
            show_about()
        elif command == CMD_LOG:
            open_log()
        elif command == CMD_EXIT:
            stop_event.set()
            user32.DestroyWindow(hwnd)
    finally:
        user32.DestroyMenu(menu)


@WNDPROC
def window_proc(hwnd, message, wparam, lparam):
    if message == WM_TRAY:
        event = int(lparam) & 0xFFFF
        if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
            show_menu(hwnd)
        elif event == WM_LBUTTONDBLCLK:
            open_log()
        return 0
    if message == WM_STATUS_CHANGED:
        update_tray_state()
        return 0
    if message == WM_CLOSE:
        stop_event.set()
        user32.DestroyWindow(hwnd)
        return 0
    if message == WM_DESTROY:
        if notify_data is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(notify_data))
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, message, wparam, lparam)


def load_icon_file(resource_root: Path, filename: str) -> int | None:
    icon_path = resource_root / "assets" / filename
    if not icon_path.is_file():
        return None
    icon = user32.LoadImageW(
        None, str(icon_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE
    )
    return int(icon) if icon else None


def load_app_icons() -> tuple[int, int, set[int]]:
    resource_root = Path(
        getattr(hot.sys, "_MEIPASS", Path(__file__).resolve().parent)
    )
    active = load_icon_file(resource_root, "gamma22.ico")
    inactive = load_icon_file(resource_root, "gamma22-disabled.ico")
    loaded = {icon for icon in (active, inactive) if icon is not None}
    if active is not None:
        return active, inactive or active, loaded
    try:
        large = wintypes.HICON()
        small = wintypes.HICON()
        if shell32.ExtractIconExW(
            hot.sys.executable, 0, ctypes.byref(large), ctypes.byref(small), 1
        ):
            if large:
                user32.DestroyIcon(large)
            if small:
                fallback = int(small)
                return fallback, fallback, {fallback}
    except Exception:
        pass
    fallback = int(
        user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wintypes.LPCWSTR))
    )
    return fallback, fallback, set()


def worker() -> None:
    browser_locations = (
        (
            "Chrome",
            (
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            ),
        ),
        (
            "Edge",
            (
                Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ),
        ),
        (
            "Brave",
            (
                Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
                Path(r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe"),
                Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
                / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            ),
        ),
    )
    targets = []
    states = {}
    update_restart = UpdateRestartCoordinator()

    def publish_status() -> None:
        if not states:
            set_status("No supported browser installed")
            return
        set_status(" | ".join(f"{name}: {states[name]}" for name in states))

    for name, candidates in browser_locations:
        installed = [candidate.resolve() for candidate in candidates if candidate.is_file()]
        if not installed:
            continue
        if len(installed) > 1:
            states[name] = "multiple installations"
            publish_status()
            print(f"{name}: multiple installed executables found; skipping")
            continue
        browser = installed[0]
        states[name] = "analyzing"
        publish_status()
        try:
            generations = BrowserGenerations(browser)
            dll = hot.locate_chrome_dll(browser, None)
            plan, _added, _changed = generations.activate(dll)
            current = set(hot.running_processes_for_executable(browser))
            completed: set[int] = set()
            enabled, _switching = fix_mode()
            states[name] = (
                "applying" if enabled and current
                else "waiting" if enabled
                else "off"
            )
            targets.append(
                {
                    "name": name,
                    "browser": browser,
                    "generations": generations,
                    "completed": completed,
                    "roles": {},
                    "verified": set(),
                    "failures": {},
                    "last_update_error": None,
                }
            )
            print(
                f"{name}: watching {browser} "
                f"({dll.parent.name}/{dll.name}, {plan.dll_hash[:12]})"
            )
        except Exception as error:
            states[name] = "unsupported/error"
            print(f"{name}: ERROR: {error}")
        publish_status()

    last_enabled, initial_switching = fix_mode()
    if initial_switching:
        finish_fix_toggle()
    while not stop_event.is_set():
        enabled, _switching = fix_mode()
        mode_changed = enabled != last_enabled
        if mode_changed:
            destination = "on" if enabled else "off"
            print(f"Switching Gamma 2.2 fix {destination} for all running browsers")
            for target in targets:
                target["completed"].clear()
                target["verified"].clear()
                states[target["name"]] = f"switching {destination}"
            publish_status()
        for target in targets:
            name = target["name"]
            browser = target["browser"]
            generations = target["generations"]
            try:
                update = generations.poll()
                if update is not None:
                    update_kind, update_detail = update
                    if update_kind == "updated":
                        target["last_update_error"] = None
                        print(f"{name}: browser update detected: {update_detail}")
                        update_restart.schedule(f"{name}: {update_detail}")
                        print(
                            f"{name}: suspending new runtime writes for "
                            f"{UPDATE_RESTART_SETTLE_SECONDS:.0f} seconds before "
                            "restarting Gamma22Tray"
                        )
                    elif update_detail != target["last_update_error"]:
                        target["last_update_error"] = update_detail
                        print(
                            f"{name}: updated DLL is not currently supported; "
                            f"will retry safely ({update_detail})"
                        )
                current = set(hot.running_processes_for_executable(browser))
                target["completed"].intersection_update(current)
                target["verified"].intersection_update(current)
                target["roles"] = {
                    pid: role
                    for pid, role in target["roles"].items()
                    if pid in current
                }
                if update_restart.pending:
                    states[name] = browser_runtime_status(
                        enabled=enabled,
                        current=current,
                        roles=target["roles"],
                        verified=target["verified"],
                        completed=target["completed"],
                        active_error=generations.active_error,
                        update_pending=True,
                    )
                    publish_status()
                    continue
                for pid in sorted(current - target["completed"]):
                    try:
                        command_line = hot.process_command_line(pid)
                    except (OSError, hot.PatchError):
                        target["failures"][pid] = target["failures"].get(pid, 0) + 1
                        continue
                    if "--type=crashpad-handler" in command_line:
                        target["completed"].add(pid)
                        continue
                    if "--type=gpu-process" in command_line:
                        role = "gpu"
                    elif "--type=" not in command_line:
                        role = "browser"
                    else:
                        # Renderer and utility processes intentionally retain
                        # their pristine upstream gamma behavior.
                        target["completed"].add(pid)
                        continue
                    target["roles"][pid] = role
                    success, detail, observed_dll = hot.attach_one_multi(
                        pid,
                        generations.plans_by_dll,
                        role=role,
                        enabled=enabled,
                    )
                    if success:
                        target["completed"].add(pid)
                        target["verified"].add(pid)
                        target["failures"].pop(pid, None)
                        print(f"{name}: new {role} PID {pid}: {detail}")
                        hot.refresh_display_state(current)
                    else:
                        unsupported_observed = False
                        if observed_dll is not None:
                            try:
                                observed_plan, added = generations.register_observed(
                                    observed_dll
                                )
                                if added:
                                    target["failures"].pop(pid, None)
                                    print(
                                        f"{name}: discovered live update generation "
                                        f"{observed_dll.parent.name}/"
                                        f"{observed_plan.dll_hash[:12]}; retrying PID {pid}"
                                    )
                                    continue
                            except Exception as error:
                                unsupported_observed = True
                                detail = (
                                    f"unsupported observed DLL {observed_dll}: {error}"
                                )
                        attempts = target["failures"].get(pid, 0) + 1
                        target["failures"][pid] = attempts
                        if attach_attempt_is_final(
                            attempts, unsupported_observed=unsupported_observed
                        ):
                            target["completed"].add(pid)
                            print(
                                f"{name}: PID {pid} skipped after {attempts} attach "
                                f"attempt(s) ({detail})"
                            )
                        else:
                            print(
                                f"{name}: PID {pid} attach attempt {attempts} "
                                f"deferred ({detail})"
                            )
                states[name] = browser_runtime_status(
                    enabled=enabled,
                    current=current,
                    roles=target["roles"],
                    verified=target["verified"],
                    completed=target["completed"],
                    active_error=generations.active_error,
                    update_pending=False,
                )
            except Exception as error:
                states[name] = "error"
                print(f"{name} watcher ERROR: {error}")
            publish_status()
        if update_restart.due():
            print(
                "Browser update settling period complete; restarting Gamma22Tray "
                "before attaching to the current browser generation"
            )
            set_status("Browser updated — restarting safely…")
            try:
                request_process_restart()
            except Exception as error:
                print(f"Automatic Gamma22Tray restart failed: {error}")
                set_status("Browser updated — manual restart required")
            return
        if mode_changed:
            last_enabled = enabled
            finish_fix_toggle()
            publish_status()
        stop_event.wait(0.5)


def main() -> int:
    global active_icon, inactive_icon, log_path, notify_data, owned_icons, window_handle
    if not wait_for_restart_parent(sys.argv[1:]):
        return 1
    log_path = hot.configure_background_process()
    if getattr(hot.sys, "frozen", False) and hot._instance_mutex is None:
        return 0
    hot.set_status_callback(set_status)

    instance = kernel32.GetModuleHandleW(None)
    class_name = "ChromiumGamma22TrayWindow"
    window_class = WNDCLASSW()
    window_class.lpfnWndProc = window_proc
    window_class.hInstance = instance
    window_class.lpszClassName = class_name
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        raise ctypes.WinError(ctypes.get_last_error())
    window_handle = user32.CreateWindowExW(
        0, class_name, class_name, 0, 0, 0, 0, 0, None, None, instance, None
    )
    if not window_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    active_icon, inactive_icon, owned_icons = load_app_icons()
    notify_data = NOTIFYICONDATAW()
    notify_data.cbSize = ctypes.sizeof(notify_data)
    notify_data.hWnd = window_handle
    notify_data.uID = 1
    notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    notify_data.uCallbackMessage = WM_TRAY
    notify_data.hIcon = active_icon
    notify_data.szTip = "Chromium Gamma 2.2 — Starting"
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data)):
        raise ctypes.WinError(ctypes.get_last_error())
    notify_data.uVersion = NOTIFYICON_VERSION_4
    shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(notify_data))

    threading.Thread(target=worker, name="Gamma22Watcher", daemon=True).start()
    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))
    stop_event.set()
    for icon in owned_icons:
        user32.DestroyIcon(icon)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
