#!/usr/bin/env python3
"""Run 64-bit Google Chrome or Brave with the HDR SDR gamma 2.2 patch in memory.

The browser DLL is never modified.  Chrome/Brave is launched through the
documented Windows debugging API so every descendant process pauses when
chrome.dll is mapped.  Structurally verified instructions are then changed
in that process's private memory before it can execute them.

Brave uses the same chrome.dll name and Chrome loads-trampoline layout
(verified on Brave 153.1.95.101 with 47 initializer pairs), so it shares
the Chrome runtime path. Microsoft Edge uses a separate singleton layout.

This is an experimental proof of concept.  It intentionally refuses unknown
binary layouts instead of guessing.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import struct
import subprocess
import sys

from gamma22_patcher import (
    GAMMA22_TRANSFER_FUNCTION,
    HDR_OUTPUT_HELPER_BYTES,
    PatchError,
    SRGB_GAMUT,
    SRGB_TRANSFER_FUNCTION,
    branch,
    discover_chrome_runtime_layout,
    find_all_rvas,
    make_trampoline,
    read_at,
    read_pe_sections,
    rva_to_offset,
    rel32,
    sha256,
    transfer_load,
)


if os.name != "nt":
    raise SystemExit("Gamma22 Runtime currently supports Windows x64 only.")
if struct.calcsize("P") != 8:
    raise SystemExit("Gamma22 Runtime must be run with a 64-bit Python/EXE.")


DEBUG_PROCESS = 0x00000001
EXCEPTION_DEBUG_EVENT = 1
CREATE_PROCESS_DEBUG_EVENT = 3
EXIT_PROCESS_DEBUG_EVENT = 5
LOAD_DLL_DEBUG_EVENT = 6
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
EXCEPTION_BREAKPOINT = 0x80000003
ERROR_SEM_TIMEOUT = 121
PAGE_EXECUTE_READWRITE = 0x40
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH = 260
ULONG_PTR = ctypes.c_size_t

# Verified layouts: Edge 151.0.4129.107 has 98; Edge 152.0.4191.53 has 97.
# A known count is only one check, not permission to skip structural validation.
EDGE_SINGLETON_INITIALIZER_COUNTS = frozenset({97, 98})


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ULONG_PTR),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]

class EXCEPTION_RECORD(ctypes.Structure):
    pass


EXCEPTION_RECORD._fields_ = [
    ("ExceptionCode", wintypes.DWORD),
    ("ExceptionFlags", wintypes.DWORD),
    ("ExceptionRecord", ctypes.POINTER(EXCEPTION_RECORD)),
    ("ExceptionAddress", ctypes.c_void_p),
    ("NumberParameters", wintypes.DWORD),
    ("ExceptionInformation", ULONG_PTR * 15),
]


class EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("ExceptionRecord", EXCEPTION_RECORD),
        ("dwFirstChance", wintypes.DWORD),
    ]


class CREATE_THREAD_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("hThread", wintypes.HANDLE),
        ("lpThreadLocalBase", ctypes.c_void_p),
        ("lpStartAddress", ctypes.c_void_p),
    ]


class CREATE_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("hFile", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("lpBaseOfImage", ctypes.c_void_p),
        ("dwDebugInfoFileOffset", wintypes.DWORD),
        ("nDebugInfoSize", wintypes.DWORD),
        ("lpThreadLocalBase", ctypes.c_void_p),
        ("lpStartAddress", ctypes.c_void_p),
        ("lpImageName", ctypes.c_void_p),
        ("fUnicode", wintypes.WORD),
    ]


class EXIT_THREAD_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("dwExitCode", wintypes.DWORD)]


class EXIT_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("dwExitCode", wintypes.DWORD)]


class LOAD_DLL_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("hFile", wintypes.HANDLE),
        ("lpBaseOfDll", ctypes.c_void_p),
        ("dwDebugInfoFileOffset", wintypes.DWORD),
        ("nDebugInfoSize", wintypes.DWORD),
        ("lpImageName", ctypes.c_void_p),
        ("fUnicode", wintypes.WORD),
    ]


class UNLOAD_DLL_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p)]


class OUTPUT_DEBUG_STRING_INFO(ctypes.Structure):
    _fields_ = [
        ("lpDebugStringData", ctypes.c_void_p),
        ("fUnicode", wintypes.WORD),
        ("nDebugStringLength", wintypes.WORD),
    ]


class RIP_INFO(ctypes.Structure):
    _fields_ = [("dwError", wintypes.DWORD), ("dwType", wintypes.DWORD)]


class DEBUG_EVENT_UNION(ctypes.Union):
    _fields_ = [
        ("Exception", EXCEPTION_DEBUG_INFO),
        ("CreateThread", CREATE_THREAD_DEBUG_INFO),
        ("CreateProcessInfo", CREATE_PROCESS_DEBUG_INFO),
        ("ExitThread", EXIT_THREAD_DEBUG_INFO),
        ("ExitProcess", EXIT_PROCESS_DEBUG_INFO),
        ("LoadDll", LOAD_DLL_DEBUG_INFO),
        ("UnloadDll", UNLOAD_DLL_DEBUG_INFO),
        ("DebugString", OUTPUT_DEBUG_STRING_INFO),
        ("RipInfo", RIP_INFO),
    ]


class DEBUG_EVENT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("dwDebugEventCode", wintypes.DWORD),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
        ("u", DEBUG_EVENT_UNION),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL
kernel32.ContinueDebugEvent.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
kernel32.ContinueDebugEvent.restype = wintypes.BOOL
kernel32.DebugSetProcessKillOnExit.argtypes = [wintypes.BOOL]
kernel32.DebugSetProcessKillOnExit.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetFinalPathNameByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.VirtualProtectEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.VirtualProtectEx.restype = wintypes.BOOL
kernel32.FlushInstructionCache.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
]
kernel32.FlushInstructionCache.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
advapi32.GetTokenInformation.restype = wintypes.BOOL

wait_for_debug_event = getattr(kernel32, "WaitForDebugEventEx", kernel32.WaitForDebugEvent)
wait_for_debug_event.argtypes = [ctypes.POINTER(DEBUG_EVENT), wintypes.DWORD]
wait_for_debug_event.restype = wintypes.BOOL


def win_error(action: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{action}: {ctypes.FormatError(code).strip()}")


def close_handle(handle: wintypes.HANDLE | None) -> None:
    if handle:
        kernel32.CloseHandle(handle)


def is_process_elevated() -> bool:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise win_error("OpenProcessToken")
    try:
        elevated = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_ELEVATION_CLASS,
            ctypes.byref(elevated),
            ctypes.sizeof(elevated),
            ctypes.byref(returned),
        ):
            raise win_error("GetTokenInformation(TokenElevation)")
        return bool(elevated.value)
    finally:
        close_handle(token)


def normalized_path(path: Path | str) -> str:
    value = str(path)
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def running_processes_for_executable(executable: Path) -> list[int]:
    """Return PIDs whose full image path exactly matches one executable."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise win_error("CreateToolhelp32Snapshot")
    expected = normalized_path(executable)
    result: list[int] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == executable.name.lower():
                process = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION,
                    False,
                    entry.th32ProcessID,
                )
                if process:
                    try:
                        capacity = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(capacity.value)
                        if kernel32.QueryFullProcessImageNameW(
                            process, 0, buffer, ctypes.byref(capacity)
                        ) and normalized_path(buffer.value) == expected:
                            result.append(int(entry.th32ProcessID))
                    finally:
                        close_handle(process)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        close_handle(snapshot)
    return result


def _brave_exe_candidates() -> tuple[Path, ...]:
    """Return known Brave executable locations, including per-user installs."""
    candidates = [
        Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
        Path(r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "BraveSoftware"
            / "Brave-Browser"
            / "Application"
            / "brave.exe"
        )
    return tuple(candidates)


def is_installed_chrome(browser: Path) -> bool:
    """Return whether a browser path is a normally installed Chrome or Brave.

    Kept under the historic name for backward compatibility. Brave shares
    Chrome's runtime layout and default-profile behavior, so installed Brave
    (Program Files or per-user LOCALAPPDATA) counts as installed here.
    """
    static_installed = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    )
    candidates: list[Path] = list(static_installed) + list(_brave_exe_candidates())
    return any(
        normalized_path(browser) == normalized_path(candidate)
        for candidate in candidates
        if candidate.is_file()
    )


def is_installed_brave(browser: Path) -> bool:
    """Return whether a browser path is a normally installed Brave."""
    return any(
        normalized_path(browser) == normalized_path(candidate)
        for candidate in _brave_exe_candidates()
        if candidate.is_file()
    )


def path_from_handle(handle: wintypes.HANDLE | None) -> Path | None:
    if not handle:
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        return None
    value = buffer.value
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def read_memory(process: wintypes.HANDLE, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    transferred = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        process, ctypes.c_void_p(address), buffer, size, ctypes.byref(transferred)
    ):
        raise win_error(f"ReadProcessMemory at 0x{address:X}")
    if transferred.value != size:
        raise PatchError(
            f"Short process-memory read at 0x{address:X}: "
            f"{transferred.value}/{size} bytes"
        )
    return buffer.raw


def write_memory(process: wintypes.HANDLE, address: int, data: bytes) -> None:
    old_protection = wintypes.DWORD()
    if not kernel32.VirtualProtectEx(
        process,
        ctypes.c_void_p(address),
        len(data),
        PAGE_EXECUTE_READWRITE,
        ctypes.byref(old_protection),
    ):
        raise win_error(f"VirtualProtectEx at 0x{address:X}")
    try:
        transferred = ctypes.c_size_t()
        buffer = ctypes.create_string_buffer(data)
        if not kernel32.WriteProcessMemory(
            process,
            ctypes.c_void_p(address),
            buffer,
            len(data),
            ctypes.byref(transferred),
        ):
            raise win_error(f"WriteProcessMemory at 0x{address:X}")
        if transferred.value != len(data):
            raise PatchError(
                f"Short process-memory write at 0x{address:X}: "
                f"{transferred.value}/{len(data)} bytes"
            )
    finally:
        ignored = wintypes.DWORD()
        kernel32.VirtualProtectEx(
            process,
            ctypes.c_void_p(address),
            len(data),
            old_protection.value,
            ctypes.byref(ignored),
        )


@dataclass(frozen=True)
class MemoryWrite:
    label: str
    rva: int
    original: bytes
    patched: bytes


@dataclass
class RuntimePlan:
    dll: Path
    dll_hash: str
    layout: dict
    checks: list[tuple[str, int, bytes]]
    writes: list[MemoryWrite]


def discover_edge_runtime_layout(dll: Path) -> dict:
    """Discover Edge's singleton gamma and ScreenWin usage-loop layout."""
    sections = read_pe_sections(dll)
    gamut_rvas = set(find_all_rvas(dll, sections, SRGB_GAMUT))
    srgb_rvas = set(find_all_rvas(dll, sections, SRGB_TRANSFER_FUNCTION))
    gamma_rvas = set(find_all_rvas(dll, sections, GAMMA22_TRANSFER_FUNCTION))
    gamut_stride = (len(SRGB_GAMUT) + 7) & ~7
    transfer_stride = (len(SRGB_TRANSFER_FUNCTION) + 7) & ~7
    constant_blocks = [
        (gamut, gamut + gamut_stride, gamut + gamut_stride + transfer_stride)
        for gamut in gamut_rvas
        if gamut + gamut_stride in srgb_rvas
        and gamut + gamut_stride + transfer_stride in gamma_rvas
    ]
    if len(constant_blocks) != 1:
        raise PatchError(
            "Expected one contiguous Edge BT.709/sRGB/gamma22 constant block; "
            f"found {len(constant_blocks)}"
        )
    gamut_rva, srgb_rva, gamma_rva = constant_blocks[0]

    try:
        _name, text_rva, _virtual_size, text_offset, text_size = next(
            section for section in sections if section[0] == ".text"
        )
    except StopIteration as error:
        raise PatchError("Edge DLL has no .text section") from error
    with dll.open("rb") as stream:
        stream.seek(text_offset)
        text = stream.read(text_size)

    def rip_target(position: int, displacement_offset: int, size: int) -> int:
        displacement = struct.unpack_from(
            "<i", text, position + displacement_offset
        )[0]
        return text_rva + position + size + displacement

    constructors: list[tuple[int, int, int]] = []
    srgb_load_rvas: set[int] = set()
    position = 0
    while True:
        position = text.find(b"\x48\x8D\x0D", position)
        if position < 0:
            break
        if position + 7 <= len(text) and rip_target(position, 3, 7) == srgb_rva:
            srgb_load_rvas.add(text_rva + position)
        if (
            position + 26 <= len(text)
            and text[position + 7 : position + 10] == b"\x48\x8D\x15"
            and text[position + 14] == 0xE8
            and text[position + 19 : position + 22] == b"\x48\x89\x05"
            and rip_target(position, 3, 7) == srgb_rva
            and rip_target(position + 7, 3, 7) == gamut_rva
        ):
            constructors.append(
                (
                    text_rva + position,
                    rip_target(position + 14, 1, 5),
                    rip_target(position + 19, 3, 7),
                )
            )
        position += 1
    constructor_tuples = {(factory, pointer) for _rva, factory, pointer in constructors}
    if (
        len(constructors) not in EDGE_SINGLETON_INITIALIZER_COUNTS
        or len(constructor_tuples) != 1
    ):
        raise PatchError(
            "Unexpected Edge singleton constructors: "
            f"{len(constructors)} initializer(s), {len(constructor_tuples)} factory/pointer tuple(s)"
        )
    singleton_factory_rva, singleton_pointer_rva = constructor_tuples.pop()

    # Accepting both counts must not turn a partially recognized 98-initializer
    # layout into an apparently valid 97-initializer layout. Account for every
    # load of this canonical sRGB constant and every store to its singleton.
    if srgb_load_rvas != {rva for rva, _factory, _pointer in constructors}:
        raise PatchError("Unrecognized Edge sRGB singleton initializer load")
    singleton_store_rvas: set[int] = set()
    position = 0
    while True:
        position = text.find(b"\x48\x89\x05", position)
        if position < 0:
            break
        if (
            position + 7 <= len(text)
            and rip_target(position, 3, 7) == singleton_pointer_rva
        ):
            singleton_store_rvas.add(text_rva + position)
        position += 1
    if singleton_store_rvas != {
        rva + 19 for rva, _factory, _pointer in constructors
    }:
        raise PatchError("Unrecognized Edge sRGB singleton initializer store")

    loop_candidates: list[tuple[int, int, int, bytes]] = []
    loop_prefix = b"\x45\x31\xC0\x4C\x8D\x8C\x24"
    position = 0
    while True:
        position = text.find(loop_prefix, position)
        if position < 0:
            break
        candidate = text[position : position + 51]
        position += 1
        if (
            len(candidate) != 51
            or candidate[11] != 0xE8
            or candidate[16:20] != b"\x48\x89\x5C\x24"
            or candidate[21:24] != b"\x48\x89\xE9"
            or candidate[24:27] != b"\x8A\x54\x24"
            or candidate[28:35] != b"\x41\xB0\x01\x4C\x8D\x8C\x24"
            or candidate[39] != 0xE8
            or candidate[44:51] != b"\x48\xFF\xC7\x48\x83\xFF\x02"
            or candidate[7:11] != candidate[35:39]
        ):
            continue
        call1_rva = text_rva + position - 1 + 11
        call2_rva = text_rva + position - 1 + 39
        call1_target = call1_rva + 5 + struct.unpack_from("<i", candidate, 12)[0]
        call2_target = call2_rva + 5 + struct.unpack_from("<i", candidate, 40)[0]
        if call1_target != call2_target:
            continue
        loop_offset = position - 1 + 47
        loop_rva = text_rva + loop_offset
        suffix = text[loop_offset : loop_offset + 25]
        if (
            len(suffix) != 25
            or suffix[:4] != b"\x48\x83\xFF\x02"
            or suffix[15:18] != b"\x48\x8D\x0D"
            or suffix[22:25] != b"\x8A\x0C\x0F"
        ):
            continue
        table_rva = loop_rva + 22 + struct.unpack_from("<i", suffix, 18)[0]
        with dll.open("rb") as stream:
            stream.seek(rva_to_offset(sections, table_rva))
            table = stream.read(3)
        if table == b"\x01\x02\x00":
            loop_candidates.append((loop_rva, table_rva, call1_target, candidate))
    if not loop_candidates:
        from edge_output_analysis import find_split_output_loop
        semantic = find_split_output_loop(dll, sections, text, text_rva)
        loop_candidates.append((semantic['loop_limit_rva'], semantic['usage_table_rva'],
                                semantic['output_helper_rva'], semantic['loop_context']))
    else:
        semantic = {}
    if len(loop_candidates) != 1:
        raise PatchError(
            f"Expected one strict Edge SDR/WCG/HDR output loop; found {len(loop_candidates)}"
        )
    loop_rva, usage_table_rva, output_helper_rva, loop_context = loop_candidates[0]
    if output_helper_rva < 0:
        raise PatchError("Invalid Edge ScreenWin output helper target")

    return {
        "kind": "edge-singleton-usage-table",
        "srgb_gamut_rva": gamut_rva,
        "srgb_transfer_rva": srgb_rva,
        "gamma22_transfer_rva": gamma_rva,
        "initializer_rvas": [rva for rva, _factory, _pointer in constructors],
        "singleton_factory_rva": singleton_factory_rva,
        "singleton_pointer_rva": singleton_pointer_rva,
        "loop_limit_rva": loop_rva,
        "usage_table_rva": usage_table_rva,
        "output_helper_rva": output_helper_rva,
        "loop_context": loop_context,
        **semantic,
    }


def make_edge_runtime_plan(dll: Path) -> RuntimePlan:
    layout = discover_edge_runtime_layout(dll)
    sections = read_pe_sections(dll)
    writes: list[MemoryWrite] = []
    with dll.open("rb") as stream:
        for index, initializer_rva in enumerate(layout["initializer_rvas"], 1):
            original = read_at(stream, rva_to_offset(sections, initializer_rva), 7)
            if original[:3] != b"\x48\x8D\x0D":
                raise PatchError(
                    f"Unexpected Edge singleton initializer at RVA 0x{initializer_rva:X}"
                )
            writes.append(
                MemoryWrite(
                    f"sRGB initializer {index}",
                    initializer_rva,
                    original,
                    b"\x48\x8D\x0D" + rel32(initializer_rva, 7, layout["gamma22_transfer_rva"]),
                )
            )
        loop_original = read_at(
            stream, rva_to_offset(sections, layout["loop_limit_rva"]), len(layout.get('loop_original', b'\x48\x83\xFF\x02'))
        )
        table_original = read_at(
            stream, rva_to_offset(sections, layout["usage_table_rva"]), 3
        )
    if loop_original != layout.get('loop_original', b"\x48\x83\xFF\x02") or table_original != b"\x01\x02\x00":
        raise PatchError("Edge ScreenWin output state is not pristine")
    writes.extend(
        (
            MemoryWrite(
                "ScreenWin usage loop limit",
                layout["loop_limit_rva"],
                loop_original,
                layout.get('loop_patched', b"\x48\x83\xFF\x03"),
            ),
            MemoryWrite(
                "ScreenWin usage table",
                layout["usage_table_rva"],
                table_original,
                b"\x00\x01\x02",
            ),
        )
    )
    checks = [
        ("sRGB gamut", layout["srgb_gamut_rva"], SRGB_GAMUT),
        ("sRGB transfer", layout["srgb_transfer_rva"], SRGB_TRANSFER_FUNCTION),
        ("gamma 2.2 transfer", layout["gamma22_transfer_rva"], GAMMA22_TRANSFER_FUNCTION),
        ("ScreenWin output helper", layout["output_helper_rva"], HDR_OUTPUT_HELPER_BYTES),
    ]
    checks.extend(layout.get('semantic_checks', []))
    return RuntimePlan(dll, sha256(dll), layout, checks, writes)


def make_runtime_plan(dll: Path) -> RuntimePlan:
    if dll.name.lower().startswith("msedge.dll"):
        return make_edge_runtime_plan(dll)
    layout = discover_chrome_runtime_layout(dll)
    sections = read_pe_sections(dll)
    color_writes: list[MemoryWrite] = []
    with dll.open("rb") as stream:
        for index, (first, second) in enumerate(layout["initializer_pairs"], 1):
            for half, instruction_rva, target_rva in (
                ("a", first, layout["gamma22_transfer_rva"]),
                ("b", second, layout["gamma22_transfer_rva"] + 12),
            ):
                original = read_at(stream, rva_to_offset(sections, instruction_rva), 7)
                color_writes.append(
                    MemoryWrite(
                        f"sRGB initializer {index}{half}",
                        instruction_rva,
                        original,
                        transfer_load(instruction_rva, target_rva, original),
                    )
                )

    trampoline_recipe = {
        "hdr_output": {
            "cave_rva": hex(layout["cave_rva"]),
            "cave_size": layout["cave_size"],
            "set_output_call_rva": hex(layout["set_output_call_rva"]),
            "resume_rva": hex(layout["resume_rva"]),
        }
    }
    trampoline = make_trampoline(trampoline_recipe)
    cave_original = b"\xCC" * layout["cave_size"]
    cave_patched = trampoline + cave_original[len(trampoline) :]
    writes = color_writes + [
        MemoryWrite(
            "SDR scRGB/F16 trampoline",
            layout["cave_rva"],
            cave_original,
            cave_patched,
        ),
        MemoryWrite(
            "ScreenWin SDR output hook",
            layout["hook_rva"],
            layout["hook_original"],
            branch(0xE9, layout["hook_rva"], layout["cave_rva"]),
        ),
    ]
    checks = [
        ("sRGB gamut", layout["srgb_gamut_rva"], SRGB_GAMUT),
        ("sRGB transfer", layout["srgb_transfer_rva"], SRGB_TRANSFER_FUNCTION),
        ("gamma 2.2 transfer", layout["gamma22_transfer_rva"], GAMMA22_TRANSFER_FUNCTION),
        ("ScreenWin output helper", layout["set_output_call_rva"], HDR_OUTPUT_HELPER_BYTES),
    ]
    return RuntimePlan(dll, sha256(dll), layout, checks, writes)


def patch_loaded_module(
    process: wintypes.HANDLE, pid: int, module_base: int, plan: RuntimePlan
) -> str:
    for label, rva, expected in plan.checks:
        actual = read_memory(process, module_base + rva, len(expected))
        if actual != expected:
            raise PatchError(
                f"PID {pid}: remote {label} failed verification at RVA 0x{rva:X}"
            )

    current = [
        read_memory(process, module_base + item.rva, len(item.original))
        for item in plan.writes
    ]
    if all(data == item.patched for data, item in zip(current, plan.writes)):
        return "already patched"
    mismatches = [
        item.label
        for data, item in zip(current, plan.writes)
        if data != item.original
    ]
    if mismatches:
        raise PatchError(
            f"PID {pid}: module is neither pristine nor fully patched; "
            f"first mismatch: {mismatches[0]}"
        )

    completed: list[MemoryWrite] = []
    try:
        # The hook is the final write, so an incomplete operation cannot jump
        # into a partially populated trampoline.
        for item in plan.writes:
            write_memory(process, module_base + item.rva, item.patched)
            completed.append(item)
        if not kernel32.FlushInstructionCache(process, None, 0):
            raise win_error("FlushInstructionCache")
    except Exception:
        rollback_failed = False
        for item in reversed(completed):
            try:
                write_memory(process, module_base + item.rva, item.original)
            except Exception:
                rollback_failed = True
        kernel32.FlushInstructionCache(process, None, 0)
        if rollback_failed:
            kernel32.TerminateProcess(process, 0xE2)
        raise

    verified = [
        read_memory(process, module_base + item.rva, len(item.patched)) == item.patched
        for item in plan.writes
    ]
    if not all(verified):
        kernel32.TerminateProcess(process, 0xE3)
        raise PatchError(f"PID {pid}: post-write verification failed")
    return "patched"


def locate_chrome_dll(browser: Path, explicit: Path | None) -> Path:
    """Locate chrome.dll/msedge.dll for chrome.exe, brave.exe or msedge.exe.

    brave.exe uses chrome.dll in a versioned subdirectory, exactly like
    chrome.exe (e.g. Application/153.1.95.101/chrome.dll), so both share
    the ("chrome.dll",) lookup. Only msedge.exe uses msedge.dll.
    """
    if explicit:
        dll = explicit.resolve()
        if not dll.is_file():
            raise PatchError(f"Browser DLL does not exist: {dll}")
        return dll
    dll_names = ({"msedge.exe": "msedge.dll", "vivaldi.exe": "vivaldi.dll"}.get(browser.name.lower(), "chrome.dll"),)
    direct_matches = [
        browser.parent / name for name in dll_names if (browser.parent / name).is_file()
    ]
    if len(direct_matches) == 1:
        return direct_matches[0].resolve()
    matches = [
        path
        for name in dll_names
        for path in browser.parent.glob(f"**/{name}")
        if not path.name.endswith(".gamma22-original")
    ]
    if len(matches) == 1:
        return matches[0].resolve()
    if not matches:
        raise PatchError(f"No {'/'.join(dll_names)} found below {browser.parent}")
    versioned_matches = []
    for path in matches:
        try:
            version = tuple(int(part) for part in path.parent.name.split("."))
        except ValueError:
            continue
        if len(version) == 4:
            versioned_matches.append((version, path))
    if versioned_matches:
        highest_version = max(version for version, _path in versioned_matches)
        highest_matches = [
            path for version, path in versioned_matches if version == highest_version
        ]
        if len(highest_matches) == 1:
            return highest_matches[0].resolve()
    raise PatchError(
        "More than one browser DLL was found; select one explicitly with --dll"
    )


def launch_debugged(command: list[str], cwd: Path) -> tuple[int, int]:
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    process_info = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
    if not kernel32.CreateProcessW(
        str(command[0]),
        command_line,
        None,
        None,
        False,
        DEBUG_PROCESS,
        None,
        str(cwd),
        ctypes.byref(startup),
        ctypes.byref(process_info),
    ):
        raise win_error("CreateProcessW")
    pid = int(process_info.dwProcessId)
    close_handle(process_info.hThread)
    close_handle(process_info.hProcess)
    return pid, int(process_info.dwThreadId)


def run_debug_loop(command: list[str], plan: RuntimePlan) -> int:
    expected_dll = normalized_path(plan.dll)
    root_pid, _root_tid = launch_debugged(command, Path(command[0]).parent)
    # Keep the monitor and browser as one correctness unit.  If the monitor is
    # closed or crashes, Chrome must not continue and later create unpatched
    # renderer processes that could make SDR gamma switch unpredictably.
    if not kernel32.DebugSetProcessKillOnExit(True):
        raise win_error("DebugSetProcessKillOnExit")
    print(f"Chrome started as PID {root_pid}; waiting for chrome.dll load events...")

    processes: dict[int, wintypes.HANDLE] = {}
    initial_breakpoint_seen: set[int] = set()
    patched_pids: set[int] = set()
    fatal_error: Exception | None = None

    interrupted = False
    try:
        while processes or not patched_pids:
            event = DEBUG_EVENT()
            if not wait_for_debug_event(ctypes.byref(event), 500):
                error = ctypes.get_last_error()
                if error == ERROR_SEM_TIMEOUT:
                    continue
                raise win_error("WaitForDebugEvent")

            pid = int(event.dwProcessId)
            tid = int(event.dwThreadId)
            continue_status = DBG_CONTINUE
            file_to_close: wintypes.HANDLE | None = None
            thread_to_close: wintypes.HANDLE | None = None
            try:
                if event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT:
                    info = event.CreateProcessInfo
                    processes[pid] = info.hProcess
                    file_to_close = info.hFile
                    thread_to_close = info.hThread
                elif event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT:
                    info = event.LoadDll
                    file_to_close = info.hFile
                    loaded_path = path_from_handle(info.hFile)
                    if (
                        loaded_path is not None
                        and normalized_path(loaded_path) == expected_dll
                    ):
                        process = processes.get(pid)
                        if not process:
                            raise PatchError(f"PID {pid}: no debug process handle")
                        module_base = int(info.lpBaseOfDll)
                        state = patch_loaded_module(process, pid, module_base, plan)
                        patched_pids.add(pid)
                        print(
                            f"  PID {pid}: chrome.dll @ 0x{module_base:X} - {state} "
                            f"({len(plan.layout['initializer_pairs']) * 2} gamma loads + SDR F16 hook)"
                        )
                elif event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT:
                    code = int(event.Exception.ExceptionRecord.ExceptionCode)
                    if code == EXCEPTION_BREAKPOINT and pid not in initial_breakpoint_seen:
                        initial_breakpoint_seen.add(pid)
                        continue_status = DBG_CONTINUE
                    else:
                        continue_status = DBG_EXCEPTION_NOT_HANDLED
                elif event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT:
                    handle = processes.pop(pid, None)
                    close_handle(handle)
                    initial_breakpoint_seen.discard(pid)
            except Exception as error:
                fatal_error = error
                print(f"Runtime patch failed: {error}", file=sys.stderr)
                for handle in processes.values():
                    kernel32.TerminateProcess(handle, 0xE1)
            finally:
                close_handle(file_to_close)
                close_handle(thread_to_close)
                if not kernel32.ContinueDebugEvent(pid, tid, continue_status):
                    if fatal_error is None:
                        fatal_error = win_error("ContinueDebugEvent")

            if fatal_error is not None and not processes:
                break
    except KeyboardInterrupt:
        interrupted = True
        print(
            "\nRuntime monitor stopped; closing the associated Chrome process tree."
        )
        for handle in processes.values():
            kernel32.TerminateProcess(handle, 0xE0)
    finally:
        for handle in processes.values():
            close_handle(handle)

    if interrupted:
        return 130
    if fatal_error is not None:
        print(f"Fatal runtime error: {fatal_error}", file=sys.stderr)
        return 2
    if len(patched_pids) < 2:
        print(
            "ERROR: Only Chrome's short-lived bootstrap process was patched. "
            "The real browser probably handed off to an existing instance or "
            "re-launched outside the monitor. Close every Chrome process and "
            "make sure Gamma22Runtime is not running as administrator.",
            file=sys.stderr,
        )
        return 3
    print(f"Chrome exited. Patched {len(patched_pids)} process(es).")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Launch 64-bit Google Chrome or Brave with the SDR gamma 2.2 fix "
            "applied only in process memory. chrome.dll is not modified."
        )
    )
    parser.add_argument(
        "browser",
        type=Path,
        nargs="?",
        help="Path to chrome.exe, brave.exe or portable launcher (auto-detected beside the runtime EXE)",
    )
    parser.add_argument("--dll", type=Path, help="Explicit path to the matching chrome.dll")
    profile_group = parser.add_mutually_exclusive_group()
    profile_group.add_argument(
        "--user-data-dir",
        type=Path,
        help="Profile directory (defaults to an isolated profile under LOCALAPPDATA)",
    )
    profile_group.add_argument(
        "--use-default-profile",
        action="store_true",
        help="Use the browser's normal profile (the default for an installed Chrome/Brave)",
    )
    profile_group.add_argument(
        "--isolated-profile",
        action="store_true",
        help="Force the isolated LOCALAPPDATA profile even for an installed Chrome/Brave",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Verify structural compatibility without starting the browser",
    )
    return parser


def auto_detect_browser() -> Path:
    program_dir = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    roots = {Path.cwd().resolve(), program_dir}
    candidates = {
        candidate.resolve()
        for root in roots
        for name in ("GoogleChromePortable.exe", "chrome.exe", "brave.exe", "msedge.exe")
        if (candidate := root / name).is_file()
    }
    if len(candidates) == 1:
        return candidates.pop()
    if len(candidates) > 1:
        raise PatchError("More than one local browser candidate was found; specify one explicitly")

    installed = {
        path.resolve()
        for path in (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            *_brave_exe_candidates(),
        )
        if path.is_file()
    }
    if len(installed) == 1:
        return installed.pop()
    if not installed:
        raise PatchError(
            "No browser was supplied and Chrome/Brave/Edge was not found beside "
            "the runtime launcher or in Program Files"
        )
    raise PatchError("More than one installed browser was found; specify one explicitly")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    args, extra = build_argument_parser().parse_known_args(argv)
    try:
        browser = args.browser.resolve() if args.browser else auto_detect_browser()
    except PatchError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if not browser.is_file():
        print(f"ERROR: Browser executable does not exist: {browser}", file=sys.stderr)
        return 2
    try:
        dll = locate_chrome_dll(browser, args.dll)
        print(f"Analyzing: {dll}")
        plan = make_runtime_plan(dll)
    except (PatchError, OSError, struct.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    layout = plan.layout
    print(f"SHA-256: {plan.dll_hash}")
    if "initializer_pairs" in layout:
        print(
            "Verified layout: "
            f"{len(layout['initializer_pairs'])} BT.709/sRGB initializers, "
            f"hook RVA 0x{layout['hook_rva']:X}, cave RVA 0x{layout['cave_rva']:X}"
        )
    else:
        print(
            "Verified layout: "
            f"{len(layout['initializer_rvas'])} BT.709/sRGB initializers, "
            f"loop RVA 0x{layout['loop_limit_rva']:X}"
        )
    print("Disk mode: read-only (all changes will be private process memory)")

    if args.scan_only:
        print("Compatible: runtime patch can be applied safely.")
        return 0

    try:
        if is_process_elevated():
            print(
                "ERROR: Gamma22Runtime is running with administrator rights. "
                "Installed Chrome deliberately re-launches itself unelevated "
                "and would escape the runtime monitor. Start this EXE by "
                "double-clicking it in Explorer or use a non-administrator terminal.",
                file=sys.stderr,
            )
            return 2
    except OSError as error:
        print(f"ERROR: Cannot verify process elevation: {error}", file=sys.stderr)
        return 2

    extra = list(extra)
    if extra and extra[0] == "--":
        extra.pop(0)
    command = [str(browser)]
    is_portableapps = browser.name.lower() == "googlechromeportable.exe"
    use_default_profile = args.use_default_profile or (
        is_installed_chrome(browser)
        and not args.isolated_profile
        and args.user_data_dir is None
    )
    if args.user_data_dir:
        command.append(f"--user-data-dir={args.user_data_dir.resolve()}")
    elif not is_portableapps and not use_default_profile:
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        )
        if browser.name.lower() == "brave.exe":
            profile = local_app_data / "Gamma22Runtime" / "BraveProfile"
        elif browser.name.lower() == "msedge.exe":
            profile = local_app_data / "Gamma22Runtime" / "EdgeProfile"
        else:
            profile = local_app_data / "Gamma22Runtime" / "ChromeProfile"
        command.append(f"--user-data-dir={profile.resolve()}")
    command.extend(extra)

    if use_default_profile:
        try:
            running = running_processes_for_executable(browser)
        except OSError as error:
            print(f"ERROR: Cannot check existing browser processes: {error}", file=sys.stderr)
            return 2
        if running:
            print(
                f"ERROR: Standard {browser.name} is already running from this installation "
                f"(PID: {', '.join(str(pid) for pid in running)}). Close every {browser.name} "
                "window and background process, then double-click Gamma22Runtime.exe again.",
                file=sys.stderr,
            )
            return 2

    try:
        return run_debug_loop(command, plan)
    except (PatchError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    exit_code = main()
    if exit_code and getattr(sys, "frozen", False) and len(sys.argv) == 1:
        try:
            input("\nPress Enter to close this window...")
        except (EOFError, KeyboardInterrupt):
            pass
    raise SystemExit(exit_code)
