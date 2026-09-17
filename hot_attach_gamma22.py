#!/usr/bin/env python3
"""Experimental no-restart Gamma22 hot-attach for an existing Chrome/Brave tree.

Brave shares Chrome's chrome.dll name and loads-trampoline layout, so the
same Chrome runtime plan applies. Edge uses a separate singleton layout.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct
import sys
import threading
import time

from gamma22_patcher import (
    GAMMA22_TRANSFER_FUNCTION,
    PatchError,
    SRGB_GAMUT,
    SRGB_TRANSFER_FUNCTION,
)
from runtime_gamma22 import (
    CREATE_PROCESS_DEBUG_EVENT,
    DBG_CONTINUE,
    DBG_EXCEPTION_NOT_HANDLED,
    DEBUG_EVENT,
    ERROR_SEM_TIMEOUT,
    EXCEPTION_BREAKPOINT,
    EXCEPTION_DEBUG_EVENT,
    EXIT_PROCESS_DEBUG_EVENT,
    LOAD_DLL_DEBUG_EVENT,
    auto_detect_browser,
    close_handle,
    kernel32,
    locate_chrome_dll,
    make_runtime_plan,
    normalized_path,
    path_from_handle,
    read_memory,
    running_processes_for_executable,
    wait_for_debug_event,
    win_error,
    write_memory,
)


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_COMMAND_LINE_INFORMATION = 60
ERROR_ALREADY_EXISTS = 183
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_IMAGE = 0x1000000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
WRITABLE_PROTECTIONS = {0x04, 0x08, 0x40, 0x80}
WM_DISPLAYCHANGE = 0x007E
SMTO_ABORTIFHUNG = 0x0002

kernel32.DebugActiveProcess.argtypes = [wintypes.DWORD]
kernel32.DebugActiveProcess.restype = wintypes.BOOL
kernel32.DebugActiveProcessStop.argtypes = [wintypes.DWORD]
kernel32.DebugActiveProcessStop.restype = wintypes.BOOL
kernel32.CheckRemoteDebuggerPresent.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.BOOL),
]
kernel32.CheckRemoteDebuggerPresent.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

ntdll = ctypes.WinDLL("ntdll")
ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
ntdll.NtQueryInformationProcess.restype = wintypes.LONG


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.FindWindowExW.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
]
user32.FindWindowExW.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ULONG_PTR := ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.MessageBoxW.argtypes = [
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.UINT,
]
user32.MessageBoxW.restype = ctypes.c_int

_instance_mutex = None
_background_log_path: Path | None = None
_status_callback = None


def set_status_callback(callback) -> None:
    global _status_callback
    _status_callback = callback


def report_status(status: str) -> None:
    if _status_callback is not None:
        _status_callback(status)


def configure_background_process() -> Path | None:
    """Give a windowed PyInstaller build persistent diagnostics and one instance."""
    global _background_log_path, _instance_mutex
    if not getattr(sys, "frozen", False):
        return None

    if _instance_mutex is not None:
        return _background_log_path

    log_root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChromiumGamma22"
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / "Gamma22HotAttach.log"
    _background_log_path = log_path
    log_stream = log_path.open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log_stream
    if sys.stderr is None:
        sys.stderr = log_stream
    print(f"\n--- Gamma22HotAttach started {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(
        None, False, "Local\\ChromiumGamma22HotAttach-SingleInstance"
    )
    if not mutex:
        raise win_error("CreateMutexW")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        close_handle(mutex)
        user32.MessageBoxW(
            None,
            "Gamma 2.2 watcher is already running.",
            "Chromium HDR SDR Gamma 2.2",
            0x00000040,
        )
        return log_path
    _instance_mutex = mutex
    return log_path


def process_command_line(pid: int) -> str:
    """Read a process command line without WMI or PowerShell."""
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise win_error(f"OpenProcess({pid}) for command line")
    try:
        needed = wintypes.ULONG()
        ntdll.NtQueryInformationProcess(
            process,
            PROCESS_COMMAND_LINE_INFORMATION,
            None,
            0,
            ctypes.byref(needed),
        )
        if not needed.value:
            raise PatchError(f"PID {pid}: command-line size query failed")
        buffer = ctypes.create_string_buffer(needed.value)
        status = ntdll.NtQueryInformationProcess(
            process,
            PROCESS_COMMAND_LINE_INFORMATION,
            buffer,
            len(buffer),
            ctypes.byref(needed),
        )
        if status < 0:
            raise PatchError(
                f"PID {pid}: NtQueryInformationProcess failed "
                f"(NTSTATUS 0x{status & 0xFFFFFFFF:08X})"
            )
        value = UNICODE_STRING.from_buffer(buffer)
        return ctypes.wstring_at(value.Buffer, value.Length // 2)
    finally:
        close_handle(process)


def patch_live_srgb_objects(
    process: wintypes.HANDLE, pid: int, *, restore: bool = False
) -> list[int]:
    """Patch already-created canonical sRGB Skia data in private writable memory.

    Patching chrome.dll changes future ColorSpace conversions, but an attached
    process can retain a cached SkColorSpace created before the attach. Match
    the complete 64-byte [transfer function][BT.709 gamut] tuple so unrelated
    floats and wide-gamut/HDR color spaces cannot be selected.
    """
    if restore:
        original = GAMMA22_TRANSFER_FUNCTION + SRGB_GAMUT
        replacement_transfer = SRGB_TRANSFER_FUNCTION
    else:
        original = SRGB_TRANSFER_FUNCTION + SRGB_GAMUT
        replacement_transfer = GAMMA22_TRANSFER_FUNCTION
    replacement = replacement_transfer + SRGB_GAMUT
    matches: list[int] = []
    address = 0
    maximum_user_address = 0x00007FFFFFFFFFFF
    chunk_size = 4 * 1024 * 1024

    while address < maximum_user_address:
        mbi = MEMORY_BASIC_INFORMATION()
        queried = kernel32.VirtualQueryEx(
            process,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if not queried:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        next_address = base + size
        if next_address <= address:
            break

        base_protection = int(mbi.Protect) & 0xFF
        eligible = (
            int(mbi.State) == MEM_COMMIT
            and int(mbi.Type) == MEM_PRIVATE
            and not (int(mbi.Protect) & PAGE_GUARD)
            and base_protection != PAGE_NOACCESS
            and base_protection in WRITABLE_PROTECTIONS
        )
        if eligible:
            offset = 0
            carry = b""
            while offset < size:
                amount = min(chunk_size, size - offset)
                try:
                    data = read_memory(process, base + offset, amount)
                except (OSError, PatchError):
                    carry = b""
                    offset += amount
                    continue
                searchable = carry + data
                searchable_base = base + offset - len(carry)
                start = 0
                while True:
                    found = searchable.find(original, start)
                    if found < 0:
                        break
                    match_address = searchable_base + found
                    if match_address not in matches:
                        matches.append(match_address)
                    start = found + 1
                carry = searchable[-(len(original) - 1) :]
                offset += amount
        address = next_address

    for match_address in matches:
        # Revalidate the complete tuple immediately before writing. The target
        # is suspended by the debugger, but this also keeps the operation
        # fail-closed if the implementation changes later.
        if read_memory(process, match_address, len(original)) != original:
            raise PatchError(
                f"PID {pid}: live sRGB object changed at 0x{match_address:X}"
            )
        write_memory(process, match_address, replacement_transfer)
        if read_memory(process, match_address, len(replacement)) != replacement:
            raise PatchError(
                f"PID {pid}: live gamma 2.2 verification failed at "
                f"0x{match_address:X}"
            )
    return matches


def reconcile_module_for_role(
    process,
    pid: int,
    module_base: int,
    plan,
    role: str,
    *,
    enabled: bool = True,
) -> str:
    """Apply only the code writes belonging to this Chromium process role."""
    changed_to_patch = 0
    restored_to_upstream = 0
    for item in plan.writes:
        is_gamma_initializer = item.label.startswith("sRGB initializer ")
        is_output_hook = item.label in {
            "SDR scRGB/F16 trampoline",
            "ScreenWin SDR output hook",
            "ScreenWin usage loop limit",
            "ScreenWin usage table",
        }
        should_be_patched = enabled and (
            (role == "gpu" and is_gamma_initializer)
            or (role == "browser" and is_output_hook)
        )
        desired = item.patched if should_be_patched else item.original
        alternate = item.original if should_be_patched else item.patched
        address = module_base + item.rva
        current = read_memory(process, address, len(desired))
        if current == desired:
            continue
        if current != alternate:
            raise PatchError(
                f"PID {pid}: unexpected bytes for {item.label} at 0x{address:X}"
            )
        write_memory(process, address, desired)
        if read_memory(process, address, len(desired)) != desired:
            raise PatchError(
                f"PID {pid}: verification failed for {item.label} at 0x{address:X}"
            )
        if should_be_patched:
            changed_to_patch += 1
        else:
            restored_to_upstream += 1
    if (changed_to_patch or restored_to_upstream) and not kernel32.FlushInstructionCache(
        process, None, 0
    ):
        raise win_error(f"PID {pid}: FlushInstructionCache")
    return (
        f"role={role}, mode={'on' if enabled else 'off'}, "
        f"applied {changed_to_patch} role-specific code write(s), "
        f"restored {restored_to_upstream} out-of-role write(s)"
    )


def is_being_debugged(pid: int) -> bool:
    process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not process:
        raise win_error(f"OpenProcess({pid})")
    try:
        present = wintypes.BOOL()
        if not kernel32.CheckRemoteDebuggerPresent(process, ctypes.byref(present)):
            raise win_error(f"CheckRemoteDebuggerPresent({pid})")
        return bool(present.value)
    finally:
        close_handle(process)


def windows_for_processes(pids: set[int]) -> list[int]:
    result: set[int] = set()

    @WNDENUMPROC
    def collect(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids:
            result.add(int(hwnd))
        return True

    if not user32.EnumWindows(collect, 0):
        raise win_error("EnumWindows")

    # Chromium's gfx::SingletonHwnd is a message-only window.  Such windows
    # are not returned by EnumWindows and do not receive HWND_BROADCAST.
    hwnd_message = wintypes.HWND(-3)
    current = wintypes.HWND()
    while True:
        current = user32.FindWindowExW(hwnd_message, current, None, None)
        if not current:
            break
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(current, ctypes.byref(pid))
        if int(pid.value) in pids:
            result.add(int(current))
    return sorted(result)


def refresh_display_state(pids: set[int]) -> int:
    windows = windows_for_processes(pids)
    delivered = 0
    for hwnd in windows:
        message_result = ULONG_PTR()
        if user32.SendMessageTimeoutW(
            wintypes.HWND(hwnd),
            WM_DISPLAYCHANGE,
            0,
            0,
            SMTO_ABORTIFHUNG,
            2000,
            ctypes.byref(message_result),
        ):
            delivered += 1
    return delivered


def module_matches_plan(process, module_base: int, plan) -> bool:
    """Return whether one loaded module safely matches a cached runtime plan."""
    try:
        for _label, rva, expected in plan.checks:
            if read_memory(process, module_base + rva, len(expected)) != expected:
                return False
        for item in plan.writes:
            current = read_memory(process, module_base + item.rva, len(item.original))
            if current not in (item.original, item.patched):
                return False
    except (OSError, PatchError):
        return False
    return True


def loaded_image_bases(process) -> list[int]:
    """Enumerate unique loaded image allocation bases without module handles."""
    result: list[int] = []
    seen: set[int] = set()
    address = 0
    maximum_user_address = 0x00007FFFFFFFFFFF
    while address < maximum_user_address:
        mbi = MEMORY_BASIC_INFORMATION()
        queried = kernel32.VirtualQueryEx(
            process,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if not queried:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize)
        next_address = base + size
        if next_address <= address:
            break
        allocation_base = int(mbi.AllocationBase or 0)
        if int(mbi.State) == MEM_COMMIT and int(mbi.Type) == MEM_IMAGE:
            if allocation_base and allocation_base not in seen:
                seen.add(allocation_base)
                result.append(allocation_base)
        address = next_address
    return result


def find_matching_loaded_plan(process, plans_by_dll: dict[str, list]):
    """Find a verified Chromium module when LOAD_DLL supplied no usable path."""
    plans = [
        plan
        for generation_plans in plans_by_dll.values()
        for plan in generation_plans
    ]
    for module_base in loaded_image_bases(process):
        for plan in reversed(plans):
            if module_matches_plan(process, module_base, plan):
                return module_base, plan
    return None


def apply_plan_to_module(
    process,
    pid: int,
    module_base: int,
    plan,
    role: str,
    *,
    enabled: bool,
    restore_live: bool,
) -> str:
    patch_state = reconcile_module_for_role(
        process,
        pid,
        module_base,
        plan,
        role,
        enabled=enabled,
    )
    live_objects = patch_live_srgb_objects(
        process,
        pid,
        restore=(restore_live or not enabled or role != "gpu"),
    )
    if live_objects:
        patch_state += (
            f"; {'restored' if restore_live or not enabled or role != 'gpu' else 'patched'} "
            f"{len(live_objects)} live cached sRGB object(s)"
        )
    return f"generation={plan.dll.parent.name}/{plan.dll_hash[:12]}, {patch_state}"


def matching_plans_for_module(process, module_base: int, plans) -> list:
    """Select structurally valid plans for a module whose file handle is absent."""
    return [
        plan
        for plan in plans
        if module_matches_plan(process, module_base, plan)
    ]


def observed_dll_is_trusted(
    loaded_path: Path, plans_by_dll: dict[str, list]
) -> bool:
    """Accept replacement generations only inside the verified browser tree."""
    try:
        candidate = loaded_path.resolve(strict=True)
    except OSError:
        return False
    for generation_plans in plans_by_dll.values():
        for plan in generation_plans:
            try:
                expected = Path(plan.dll).resolve(strict=True)
            except OSError:
                continue
            if candidate.name.lower() != expected.name.lower():
                continue
            version_parts = expected.parent.name.split(".")
            version_directory = (
                len(version_parts) >= 2
                and all(part.isdigit() for part in version_parts)
            )
            trusted_root = (
                expected.parent.parent if version_directory else expected.parent
            )
            try:
                candidate.relative_to(trusted_root)
                return True
            except ValueError:
                continue
    return False


def attach_one_multi(
    pid: int,
    plans_by_dll: dict[str, list],
    *,
    role: str = "other",
    enabled: bool = True,
    restore_live: bool = False,
) -> tuple[bool, str, Path | None]:
    """Attach once and select the correct DLL generation inside the process."""
    if not kernel32.DebugActiveProcess(pid):
        return False, str(win_error(f"DebugActiveProcess({pid})")), None
    if not kernel32.DebugSetProcessKillOnExit(False):
        kernel32.DebugActiveProcessStop(pid)
        return False, str(win_error("DebugSetProcessKillOnExit(FALSE)")), None

    process_handle = None
    patched = False
    patch_state = "not patched"
    observed_dll = None
    attached = True
    failure: str | None = None
    deadline = time.monotonic() + 10.0
    try:
        while time.monotonic() < deadline:
            event = DEBUG_EVENT()
            if not wait_for_debug_event(ctypes.byref(event), 500):
                error = ctypes.get_last_error()
                if error == ERROR_SEM_TIMEOUT:
                    continue
                failure = str(win_error("WaitForDebugEvent"))
                break

            event_pid = int(event.dwProcessId)
            tid = int(event.dwThreadId)
            continue_status = DBG_CONTINUE
            file_to_close = None
            thread_to_close = None
            process_to_close = None
            saw_initial_breakpoint = False
            try:
                if event_pid != pid:
                    # Debug events are queued per debugger thread. A late event
                    # from a previously attached Chromium/WebView process must
                    # be continued and ignored, not attributed to this target.
                    if event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT:
                        info = event.CreateProcessInfo
                        process_to_close = info.hProcess
                        file_to_close = info.hFile
                        thread_to_close = info.hThread
                    elif event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT:
                        file_to_close = event.LoadDll.hFile
                elif event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT:
                    info = event.CreateProcessInfo
                    process_handle = info.hProcess
                    file_to_close = info.hFile
                    thread_to_close = info.hThread
                elif event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT:
                    info = event.LoadDll
                    file_to_close = info.hFile
                    loaded_path = path_from_handle(info.hFile)
                    candidates = []
                    if loaded_path is not None:
                        loaded_key = normalized_path(loaded_path)
                        candidates = plans_by_dll.get(loaded_key, [])
                        if (
                            not candidates
                            and loaded_path.name.lower() in {"chrome.dll", "msedge.dll", "vivaldi.dll"}
                            and observed_dll_is_trusted(loaded_path, plans_by_dll)
                        ):
                            observed_dll = loaded_path
                    if candidates and process_handle:
                        # A path may represent more than one in-memory generation
                        # if an updater replaced a file in place. Validate every
                        # candidate against the suspended module before writing.
                        candidates = matching_plans_for_module(
                            process_handle, int(info.lpBaseOfDll), candidates
                        )
                        if not candidates:
                            # The same on-disk path may now name a replacement
                            # generation while an older plan remains cached.
                            observed_dll = loaded_path
                    if not candidates and loaded_path is None and process_handle:
                        # During DebugActiveProcess attach, sandboxed Chrome
                        # processes commonly report existing modules without a
                        # usable hFile. Match the entire checked/write layout.
                        all_plans = [
                            plan
                            for generation_plans in plans_by_dll.values()
                            for plan in generation_plans
                        ]
                        candidates = matching_plans_for_module(
                            process_handle, int(info.lpBaseOfDll), all_plans
                        )
                    if candidates:
                        if not process_handle:
                            raise PatchError(f"PID {pid}: missing debug process handle")
                        # When two cached generations are byte-for-byte layout
                        # compatible either is safe; prefer the newest inserted.
                        plan = candidates[-1]
                        patch_state = apply_plan_to_module(
                            process_handle,
                            pid,
                            int(info.lpBaseOfDll),
                            plan,
                            role,
                            enabled=enabled,
                            restore_live=restore_live,
                        )
                        patched = True
                elif event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT:
                    code = int(event.Exception.ExceptionRecord.ExceptionCode)
                    if code == EXCEPTION_BREAKPOINT:
                        continue_status = DBG_CONTINUE
                        if not patched and process_handle:
                            fallback = find_matching_loaded_plan(
                                process_handle, plans_by_dll
                            )
                            if fallback is not None:
                                module_base, plan = fallback
                                patch_state = apply_plan_to_module(
                                    process_handle,
                                    pid,
                                    module_base,
                                    plan,
                                    role,
                                    enabled=enabled,
                                    restore_live=restore_live,
                                )
                                patch_state += "; located by in-memory image scan"
                                patched = True
                        saw_initial_breakpoint = True
                    else:
                        continue_status = DBG_EXCEPTION_NOT_HANDLED
                elif event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT:
                    failure = "process exited during attach"
            except Exception as error:
                failure = str(error)
            finally:
                close_handle(file_to_close)
                close_handle(thread_to_close)
                close_handle(process_to_close)
                if not kernel32.ContinueDebugEvent(event_pid, tid, continue_status):
                    failure = str(win_error("ContinueDebugEvent"))

            if failure or saw_initial_breakpoint:
                break
    finally:
        if attached:
            if not kernel32.DebugActiveProcessStop(pid) and failure is None:
                failure = str(win_error(f"DebugActiveProcessStop({pid})"))
        close_handle(process_handle)

    if failure:
        return False, failure, observed_dll
    if not patched:
        if observed_dll is not None:
            return (
                False,
                f"unrecognized browser DLL generation: {observed_dll}",
                observed_dll,
            )
        return False, "browser DLL was not observed before the initial breakpoint", None
    return True, patch_state, observed_dll


def attach_one(
    pid: int,
    plan,
    expected_dll: str,
    *,
    role: str = "other",
    enabled: bool = True,
    restore_live: bool = False,
) -> tuple[bool, str]:
    """Backward-compatible single-generation attach used by the CLI tools."""
    success, detail, _observed = attach_one_multi(
        pid,
        {expected_dll: [plan]},
        role=role,
        enabled=enabled,
        restore_live=restore_live,
    )
    return success, detail


def attach_and_patch(
    browser: Path,
    dll: Path,
    *,
    enabled: bool = True,
    restore_live: bool = False,
) -> tuple[set[int], set[int]]:
    plan = make_runtime_plan(dll)
    expected_dll = normalized_path(dll)
    candidates = running_processes_for_executable(browser)
    if not candidates:
        raise PatchError(f"No running Chrome processes found for {browser}")

    targets: list[int] = []
    skipped_debugged: set[int] = set()
    for pid in candidates:
        try:
            if is_being_debugged(pid):
                skipped_debugged.add(pid)
            else:
                targets.append(pid)
        except OSError as error:
            print(f"  PID {pid}: cannot inspect debugger state ({error})")

    if not targets:
        raise PatchError(
            "Every matching Chrome process is already controlled by another debugger"
        )
    print(
        f"Found {len(candidates)} process(es): attaching sequentially to "
        f"{len(targets)}, skipping {len(skipped_debugged)} already monitored."
    )

    patched: set[int] = set()
    failures: dict[int, str] = {}
    for pid in targets:
        # Avoid attaching to a recycled PID that no longer belongs to this Chrome.
        if pid not in running_processes_for_executable(browser):
            failures[pid] = "process exited before attach"
            continue
        try:
            command_line = process_command_line(pid)
        except (OSError, PatchError) as error:
            failures[pid] = f"cannot identify process role: {error}"
            continue
        if "--type=gpu-process" in command_line:
            role = "gpu"
        elif "--type=" not in command_line:
            role = "browser"
        else:
            role = "other"
        success, state = attach_one(
            pid,
            plan,
            expected_dll,
            role=role,
            enabled=enabled,
            restore_live=restore_live,
        )
        if success:
            patched.add(pid)
            print(f"  PID {pid}: {state}")
        else:
            failures[pid] = state

    for pid, error in sorted(failures.items()):
        print(f"  PID {pid}: skipped/failed: {error}")
    return patched, skipped_debugged


def watch_new_processes(
    browser: Path,
    dll: Path,
    known: set[int],
    stop_event: threading.Event | None = None,
) -> None:
    """Keep future Chrome GPU/renderer processes on the in-memory patch."""
    plan = make_runtime_plan(dll)
    expected_dll = normalized_path(dll)
    completed = set(known)
    failures: dict[int, int] = {}
    print("Watching for new Chrome processes. Press Ctrl+C to stop the watcher.")
    while True:
        current = set(running_processes_for_executable(browser))
        report_status("Active — Chrome patched" if current else "Waiting for Chrome")
        completed.intersection_update(current)
        for pid in sorted(current - completed):
            try:
                command_line = process_command_line(pid)
            except (OSError, PatchError):
                failures[pid] = failures.get(pid, 0) + 1
                continue
            if "--type=crashpad-handler" in command_line:
                completed.add(pid)
                continue
            if "--type=gpu-process" in command_line:
                role = "gpu"
            elif "--type=" not in command_line:
                role = "browser"
            else:
                # Fresh renderers and utility processes must stay completely
                # upstream. They need no debugger attach once legacy test
                # patches have been cleaned by the initial pass.
                completed.add(pid)
                continue
            success, state = attach_one(
                pid,
                plan,
                expected_dll,
                role=role,
            )
            if success:
                completed.add(pid)
                failures.pop(pid, None)
                print(f"  New {role} PID {pid}: {state}")
                delivered = refresh_display_state(current)
                print(f"  Refreshed {delivered} Chrome window(s).")
            else:
                attempts = failures.get(pid, 0) + 1
                failures[pid] = attempts
                # A just-created process may not have loaded chrome.dll yet;
                # retry while it remains alive. Suppress repetitive chatter.
                if attempts == 1 or attempts % 10 == 0:
                    print(f"  PID {pid}: attach attempt {attempts} deferred ({state})")
        if stop_event is not None:
            if stop_event.wait(0.5):
                return
        else:
            time.sleep(0.5)


def main(
    argv: list[str] | None = None,
    *,
    stop_event: threading.Event | None = None,
) -> int:
    log_path = configure_background_process()
    if getattr(sys, "frozen", False) and _instance_mutex is None:
        return 0
    parser = argparse.ArgumentParser(
        description="Experimentally patch an already-running standard Chrome without restart"
    )
    parser.add_argument("browser", type=Path, nargs="?")
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--no-refresh", action="store_true")
    watch_group = parser.add_mutually_exclusive_group()
    watch_group.add_argument(
        "--watch",
        dest="watch",
        action="store_true",
        default=bool(getattr(sys, "frozen", False)),
        help="Keep running and patch Chrome child processes created later",
    )
    watch_group.add_argument(
        "--no-watch",
        dest="watch",
        action="store_false",
        help="Exit after patching the processes that already exist",
    )
    parser.add_argument(
        "--restore-live",
        action="store_true",
        help="Restore experimental live cached gamma objects to canonical sRGB",
    )
    args = parser.parse_args(argv)

    try:
        report_status("Analyzing installed Chrome")
        browser = args.browser.resolve() if args.browser else auto_detect_browser()
        dll = locate_chrome_dll(browser, args.dll)
        print(f"Browser: {browser}")
        print(f"DLL: {dll}")
        initial_processes = set(running_processes_for_executable(browser))
        if not initial_processes:
            if not args.watch:
                raise PatchError(f"No running Chrome processes found for {browser}")
            print("Chrome is not running yet; waiting for it to start.")
            report_status("Waiting for Chrome")
            watch_new_processes(browser, dll, set(), stop_event)
            return 0
        report_status("Applying Gamma 2.2 patch")
        patched, already_debugged = attach_and_patch(
            browser, dll, restore_live=args.restore_live
        )
        if not patched:
            raise PatchError("No previously unmonitored process was patched")
        print(f"Patched {len(patched)} existing process(es) without restarting Chrome.")
        if not args.no_refresh:
            delivered = refresh_display_state(patched | already_debugged)
            print(
                f"Sent WM_DISPLAYCHANGE to {delivered} Chrome window(s), "
                "including message-only windows."
            )
        print("Reload or repaint the gamma test and check the current Chrome window.")
        report_status("Active — Chrome patched")
        if args.watch:
            watch_new_processes(
                browser,
                dll,
                patched | already_debugged,
                stop_event,
            )
        return 0
    except KeyboardInterrupt:
        print("Watcher stopped; Chrome remains running with its current memory patch.")
        return 0
    except (OSError, PatchError, struct.error) as error:
        report_status(f"Error — {error}")
        print(f"ERROR: {error}", file=sys.stderr)
        if getattr(sys, "frozen", False):
            detail = f"\n\nDetails were saved to:\n{log_path}" if log_path else ""
            user32.MessageBoxW(
                None,
                f"The Gamma 2.2 watcher could not start:\n\n{error}{detail}",
                "Chromium HDR SDR Gamma 2.2",
                0x00000010,
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
