"""Stable GitHub releases, bounded downloads and recoverable onedir updates."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import math
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

REPO = 'mrsaliericz/browser-gamma-fix'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 500 * 1024 * 1024
COOLDOWN = 15 * 60
INTERVAL = 24 * 60 * 60


def version(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-([\w.-]+))?', value)
    if not match:
        raise ValueError('Invalid version')
    return (*map(int, match.group(1, 2, 3)), match.group(4) is None)


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    digest: str
    size: int


def parse_release(data, current):
    tag = data.get('tag_name', '')
    if data.get('draft') or data.get('prerelease'):
        return None
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Expected a stable release tag')
    if version(tag) <= version(current):
        return None
    assets = [a for a in data.get('assets', []) if a.get('name') == 'BrowserGammaFix-win64.zip']
    if len(assets) != 1:
        raise ValueError('Release has no unique Browser Gamma Fix ZIP')
    asset = assets[0]
    expected = f'https://github.com/{REPO}/releases/download/{tag}/BrowserGammaFix-win64.zip'
    if asset.get('browser_download_url') != expected:
        raise ValueError('Unexpected release asset URL')
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('Release has no GitHub SHA-256 digest; use manual download')
    size = asset.get('size', 0)
    if not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE:
        raise ValueError('Invalid release asset size')
    return Release(tag, expected, digest[7:].lower(), size)


class GitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {
            'github.com', 'api.github.com', 'release-assets.githubusercontent.com',
            'objects.githubusercontent.com',
        }:
            raise ValueError('Unexpected download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'BrowserGammaFix-update-check',
        'Accept': 'application/vnd.github+json' if url == API else 'application/octet-stream',
    })
    return urllib.request.build_opener(GitHubRedirects()).open(request, timeout=20)


def latest(current):
    with open_url(API) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('Oversized release metadata')
    return parse_release(json.loads(data), current)


class CheckSchedule:
    """Persist automatic attempt times; manual checks bypass cooldown."""
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        try:
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.state, dict): self.state = {}
            self.state = {k:v for k,v in self.state.items()
                          if isinstance(k,str) and isinstance(v,(int,float)) and math.isfinite(v)}
        except (OSError, ValueError):
            self.state = {}

    def claim(self, key='periodic', manual=False):
        now = self.clock()
        last = self.state.get('last', 0)
        seen = self.state.get(key, 0)
        if not manual and (0 <= now-last < COOLDOWN or 0 <= now-seen < INTERVAL):
            return False
        self.state['last'] = now
        self.state[key] = now
        # Bound size even if a browser replaces files repeatedly.
        self.state = dict(sorted(self.state.items(), key=lambda p:p[1], reverse=True)[:128])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state), encoding='utf-8')
        temp.replace(self.path)
        return True


def extract_package(archive, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Extraction destination must be new')
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError('Package exceeds extraction limits')
        seen = set()
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            if ('\\' in name or not parts or parts[0] != 'Gamma22Tray'
                    or any(p in ('.','..') or ':' in p or p.endswith((' ','.')) for p in parts)
                    or name.startswith('/') or stat.S_ISLNK(info.external_attr >> 16)
                    or any(p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',
                        *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))} for p in parts)):
                raise ValueError('Unsafe ZIP entry')
            normalized = name.rstrip('/').casefold()
            if normalized in seen: raise ValueError('Duplicate ZIP entry')
            seen.add(normalized)
        package.extractall(destination)
    root = destination / 'Gamma22Tray'
    if not (root/'Gamma22Tray.exe').is_file() or not (root/'_internal').is_dir():
        raise ValueError('Incomplete onedir package')
    return root


def validate_installation(directory):
    directory = Path(directory).resolve(strict=True)
    if (directory == Path(directory.anchor) or directory == Path.home().resolve()
            or {p.name for p in directory.iterdir()} != {'Gamma22Tray.exe','_internal'}
            or not (directory/'Gamma22Tray.exe').is_file()
            or not (directory/'_internal').is_dir()):
        raise ValueError('Use a dedicated folder containing only Gamma22Tray.exe and _internal; update this installation manually')
    for root, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs+files:
            entry=Path(root)/name
            info=entry.lstat()
            if entry.is_symlink() or getattr(info,'st_file_attributes',0) & 0x400:
                raise ValueError('Installation contains a link or junction; manual update required')
    return directory


def prepare(release, executable, enabled=True):
    executable = Path(executable).resolve(strict=True)
    if executable.name != 'Gamma22Tray.exe' or not (executable.parent/'_internal').is_dir():
        raise ValueError('Automatic update requires a complete installed Gamma22Tray folder')
    validate_installation(executable.parent)
    # The job is a sibling: neither download nor running helper is inside the
    # directory that will be renamed. No admin rights or security exclusions.
    job = Path(tempfile.mkdtemp(prefix='.gamma22-update-', dir=executable.parent.parent))
    archive = job/'package.zip'
    digest = hashlib.sha256()
    size = 0
    deadline = time.monotonic()+180
    with open_url(release.url) as source, archive.open('xb') as target:
        while chunk := source.read(256*1024):
            size += len(chunk)
            if size > release.size or time.monotonic() > deadline:
                raise ValueError('Download exceeded size or time limit')
            digest.update(chunk)
            target.write(chunk)
    if size != release.size or digest.hexdigest() != release.digest:
        raise ValueError('Downloaded ZIP failed SHA-256 verification')
    extract_package(archive, job/'payload')
    shutil.copytree(executable.parent, job/'helper')
    config = {'target':str(executable.parent), 'pid':os.getpid(),
              'tag':release.tag, 'digest':release.digest, 'enabled':bool(enabled)}
    (job/'job.json').write_text(json.dumps(config), encoding='utf-8')
    return job


def swap_package(target, staged, backup, launch):
    """Two directory renames; a failed launch rolls back without deleting data."""
    target, staged, backup = map(Path, (target, staged, backup))
    failed = backup.with_name(backup.name+'-failed')
    if backup.exists() or failed.exists():
        raise ValueError('Backup destination already exists')
    target.rename(backup)
    try:
        staged.rename(target)
        launch(target/'Gamma22Tray.exe')
    except Exception:
        if target.exists(): target.rename(failed)
        backup.rename(target)
        raise


def apply_job(job):
    """Called only by the copied helper process before tray initialization."""
    import ctypes
    from ctypes import wintypes
    job = Path(job).resolve(strict=True)
    if not job.name.startswith('.gamma22-update-'):
        raise ValueError('Invalid update job directory')
    if Path(sys.executable).resolve() != job/'helper'/'Gamma22Tray.exe':
        raise ValueError('Updater must run from its helper copy')
    config = json.loads((job/'job.json').read_text(encoding='utf-8'))
    target = Path(config['target']).resolve(strict=True)
    if target.parent != job.parent or target == job or not (target/'_internal').is_dir():
        raise ValueError('Invalid installation target')
    validate_installation(target)
    if hashlib.sha256((job/'package.zip').read_bytes()).hexdigest() != config['digest']:
        raise ValueError('Staged archive changed')
    # Re-extract verified bytes so changes to the first staging copy cannot
    # silently become the installed package.
    staged = extract_package(job/'package.zip',job/'verified')
    k = ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    k.OpenProcess.restype=wintypes.HANDLE
    k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    k.CloseHandle.argtypes=[wintypes.HANDLE]
    k.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    handle=k.OpenProcess(0x101000,False,config['pid'])
    if not handle: raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buffer))
        if not k.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)) or Path(buffer.value).resolve()!=target/'Gamma22Tray.exe':
            raise ValueError('Previous process does not match the installation')
        (job/'helper-ready').touch()
        if k.WaitForSingleObject(handle,60000)!=0:
            raise TimeoutError('Previous Browser Gamma Fix did not exit')
    finally:k.CloseHandle(handle)
    validate_installation(target)
    backup=job/'previous'
    def launch(exe):
        command = [str(exe), '--app-update-ready', str(job)]
        if not config.get('enabled', True): command.append('--start-fix-disabled')
        child = subprocess.Popen(command, cwd=target)
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            if (job/'app-ready').exists() and child.poll() is None:
                return
            if child.poll() is not None:
                raise RuntimeError('New application exited before startup confirmation')
            time.sleep(.1)
        # Only the specific newly launched updater child can be terminated.
        child.terminate()
        child.wait(timeout=10)
        raise TimeoutError('New application did not confirm startup')
    try:
        swap_package(target,staged,backup,launch)
        (job/'result.txt').write_text('Installed '+config['tag']+'; backup: '+str(backup),encoding='utf-8')
    except Exception:
        if (target/'Gamma22Tray.exe').is_file():
            command = [str(target/'Gamma22Tray.exe')]
            if not config.get('enabled',True): command.append('--start-fix-disabled')
            subprocess.Popen(command,cwd=target)
        raise


def confirm_startup(arguments, current):
    if '--app-update-ready' not in arguments:
        return
    index = arguments.index('--app-update-ready')
    job = Path(arguments[index+1]).resolve(strict=True)
    executable = Path(sys.executable).resolve()
    config = json.loads((job/'job.json').read_text(encoding='utf-8'))
    if (not job.name.startswith('.gamma22-update-') or job.parent != executable.parent.parent
            or Path(config['target']).resolve() != executable.parent
            or config['tag'].lstrip('v') != current):
        raise ValueError('Invalid application startup confirmation')
    (job/'app-ready').touch()


def start_helper(job):
    job=Path(job)
    helper=subprocess.Popen([str(job/'helper'/'Gamma22Tray.exe'),'--apply-app-update',str(job)],cwd=job)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if (job/'helper-ready').exists(): return
        if helper.poll() is not None: raise RuntimeError('Update helper failed; existing installation retained')
        time.sleep(.1)
    raise TimeoutError('Update helper did not become ready; existing installation retained')
