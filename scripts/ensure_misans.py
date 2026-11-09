#!/usr/bin/env python3
"""Require MiSans; install missing static weights from official source, then verify availability."""
import argparse
import ctypes
import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

SOURCE = Path(__file__).resolve().parents[1] / 'references' / 'misans-source.json'


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def postscript_name(path):
    """Read the actual SFNT name table, never infer font identity from its filename."""
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] not in (b'\x00\x01\x00\x00', b'OTTO'):
        raise ValueError('Not a static OpenType/TrueType font: ' + str(path))
    count = struct.unpack_from('>H', data, 4)[0]
    if len(data) < 12 + count * 16:
        raise ValueError('Truncated font table directory')
    for i in range(count):
        tag, _, offset, length = struct.unpack_from('>4sIII', data, 12 + i * 16)
        if offset + length > len(data):
            raise ValueError('Truncated font table')
        if tag == b'name':
            table = data[offset:offset + length]
    if 'table' not in locals() or len(table) < 6:
        raise ValueError('Missing name table')
    _, count, start = struct.unpack_from('>HHH', table)
    names = []
    for i in range(count):
        pid, _, _, nid, length, offset = struct.unpack_from('>HHHHHH', table, 6 + 12 * i)
        if nid == 6:
            value = table[start + offset:start + offset + length]
            names.append(value.decode('utf-16-be' if pid in (0, 3) else 'mac_roman'))
    if not names or len(set(names)) != 1:
        raise ValueError('Missing or inconsistent PostScript font name')
    return names[0]


def locations():
    system = platform.system()
    home = Path.home()
    if system == 'Darwin':
        destination = home / 'Library/Fonts'
        roots = [destination, Path('/Library/Fonts'), Path('/System/Library/Fonts')]
        cache = home / 'Library/Caches/ppt-polish'
    elif system == 'Linux':
        data = Path(os.environ.get('XDG_DATA_HOME', home / '.local/share'))
        destination = data / 'fonts/MiSans'
        roots = [data / 'fonts', home / '.fonts', Path('/usr/local/share/fonts'), Path('/usr/share/fonts')]
        cache = Path(os.environ.get('XDG_CACHE_HOME', home / '.cache')) / 'ppt-polish'
    elif system == 'Windows':
        local = os.environ.get('LOCALAPPDATA')
        windir = os.environ.get('WINDIR')
        if not local or not windir:
            raise RuntimeError('LOCALAPPDATA or WINDIR is unavailable')
        destination = Path(local) / 'Microsoft/Windows/Fonts'
        roots = [destination, Path(windir) / 'Fonts']
        cache = Path(local) / 'ppt-polish/Cache'
    else:
        raise RuntimeError('Unsupported font installation platform: ' + system)
    return destination, roots, cache


def find_fonts(roots, required):
    found = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*')):
            if path.suffix.lower() not in ('.ttf', '.otf') or 'misans' not in path.name.lower():
                continue
            try:
                name = postscript_name(path)
            except (OSError, ValueError, UnicodeError, struct.error):
                continue
            if name in required:
                found.setdefault(name, path)
    return found


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'hyperos.mi.com':
            raise ValueError('Unexpected font download redirect; verify the official source before continuing')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_verified(config, cache):
    cache.mkdir(parents=True, exist_ok=True)
    suffix = '.pdf' if config['archive_url'].endswith('.pdf') else '.zip'
    target = cache / ('MiSans-' + config['archive_sha256'][:16] + suffix)
    if target.is_file() and sha256(target) == config['archive_sha256']:
        return target
    url = urllib.parse.urlparse(config['archive_url'])
    if url.scheme != 'https' or url.hostname != 'hyperos.mi.com':
        raise ValueError('Font source must be the verified official HTTPS host')
    fd, name = tempfile.mkstemp(prefix='misans-', suffix='.part', dir=cache)
    os.close(fd)
    temporary = Path(name)
    try:
        opener = urllib.request.build_opener(OfficialRedirect())
        request = urllib.request.Request(config['archive_url'], headers={'User-Agent': 'ppt-polish/1.1.0'})
        with opener.open(request, timeout=45) as response, temporary.open('wb') as output:
            total = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > config['archive_bytes']:
                    raise ValueError('Official package size changed; re-verify before updating the manifest')
                output.write(block)
        if total != config['archive_bytes'] or sha256(temporary) != config['archive_sha256']:
            raise ValueError('Official package integrity check failed; no fonts installed')
        os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def prepare_fonts(archive, config, staging, license_path):
    """Read only manifest-listed members; never extract arbitrary archive paths."""
    if sha256(archive) != config['archive_sha256']:
        raise ValueError('Archive integrity check failed')
    prepared = {}
    with zipfile.ZipFile(archive) as z:
        for item in config['fonts']:
            member = item['archive_member']
            info = z.getinfo(member)
            if info.file_size > 32 * 1024 * 1024:
                raise ValueError('Unexpected font size')
            data = z.read(member)
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Font integrity check failed: ' + item['postscript_name'])
            target = staging / PurePosixPath(member).name
            target.write_bytes(data)
            if postscript_name(target) != item['postscript_name']:
                raise ValueError('Unexpected font identity')
            prepared[item['postscript_name']] = target
        license_data = Path(license_path).read_bytes()
        if hashlib.sha256(license_data).hexdigest() != config['license_sha256']:
            raise ValueError('License integrity check failed')
        (staging / 'MiSans-License.pdf').write_bytes(license_data)
    return prepared


def install_files(prepared, destination, missing, license_path):
    """Only add absent files; do not replace fonts or other user files."""
    destination.mkdir(parents=True, exist_ok=True)
    selected = {name: prepared[name] for name in missing}
    for source in selected.values():
        target = destination / source.name
        if target.exists() and sha256(target) != sha256(source):
            raise RuntimeError('Conflicting existing font; resolve explicitly: ' + str(target))
    license_target = destination / 'MiSans-License.pdf'
    if license_target.exists() and sha256(license_target) != sha256(license_path):
        raise RuntimeError('Existing MiSans license differs; retain and resolve explicitly')
    result = {}
    for name, source in selected.items():
        target = destination / source.name
        if not target.exists():
            with target.open('xb') as output, source.open('rb') as input_file:
                shutil.copyfileobj(input_file, output)
        if sha256(target) != sha256(source):
            raise RuntimeError('Installed file verification failed: ' + str(target))
        result[name] = target
    if not license_target.exists():
        with license_target.open('xb') as output, license_path.open('rb') as input_file:
            shutil.copyfileobj(input_file, output)
    return result


def system_check(fonts, installed):
    system = platform.system()
    if system == 'Darwin':
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        ct = ctypes.CDLL('/System/Library/Frameworks/CoreText.framework/CoreText')
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        ct.CTFontManagerCopyAvailablePostScriptNames.restype = ctypes.c_void_p
        names = ct.CTFontManagerCopyAvailablePostScriptNames()
        available = set()
        try:
            for i in range(cf.CFArrayGetCount(names)):
                buffer = ctypes.create_string_buffer(1024)
                value = cf.CFArrayGetValueAtIndex(names, i)
                if cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
                    available.add(buffer.value.decode('utf-8'))
        finally:
            cf.CFRelease(names)
        missing = set(fonts) - available
        if missing:
            raise RuntimeError('CoreText cannot resolve these fonts; check sandbox access to the font service or restart the render process and retry: ' + ', '.join(sorted(missing)))
    elif system == 'Linux':
        if installed:
            subprocess.run(['fc-cache', '-f', str(next(iter(installed.values())).parent)], check=True, timeout=60, capture_output=True)
        result = subprocess.run(['fc-list', '--format=%{postscriptname}\n'], check=True, capture_output=True, text=True, timeout=30)
        available = set(result.stdout.replace(',', '\n').splitlines())
        if set(fonts) - available:
            raise RuntimeError('Fontconfig cannot resolve all MiSans weights')
    elif system == 'Windows':
        import winreg
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
        gdi.AddFontResourceExW.restype = ctypes.c_int
        key_path = r'Software\Microsoft\Windows NT\CurrentVersion\Fonts'
        if installed:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                for name, path in installed.items():
                    winreg.SetValueEx(key, name + ' (TrueType)', 0, winreg.REG_SZ, str(path))
        for name, path in fonts.items():
            if not gdi.AddFontResourceExW(str(path), 0, None):
                raise RuntimeError('Windows could not register font: ' + name)
        ctypes.windll.user32.SendNotifyMessageW(0xFFFF, 0x001D, 0, 0)


def ensure(check_only=False):
    config = json.loads(SOURCE.read_text(encoding='utf-8'))
    required = {item['postscript_name'] for item in config['fonts']}
    destination, roots, cache = locations()
    found = find_fonts(roots, required)
    missing = required - set(found)
    installed = {}
    if missing:
        if check_only:
            raise RuntimeError('MiSans is missing: ' + ', '.join(sorted(missing)))
        print('MiSans missing: downloading from official source. License: ' + config['license_url'], file=sys.stderr, flush=True)
        archive = download_verified(config, cache)
        license_path = download_verified({'archive_url': config['license_url'], 'archive_sha256': config['license_sha256'], 'archive_bytes': config['license_bytes']}, cache)
        with tempfile.TemporaryDirectory(prefix='misans-') as temporary:
            staging = Path(temporary)
            prepared = prepare_fonts(archive, config, staging, license_path)
            installed = install_files(prepared, destination, missing, staging / 'MiSans-License.pdf')
        found = find_fonts(roots, required)
        if required - set(found):
            raise RuntimeError('MiSans installation is incomplete')
    system_check(found, installed)
    return {'status': 'ready', 'font': 'MiSans', 'weights': len(found), 'installed_now': sorted(installed), 'paths': {k: str(v) for k, v in sorted(found.items())}, 'next': 'Verify MiSans and its weights in the actual presentation renderer; restart it if needed.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='Diagnostic only; fail if missing without downloading')
    args = parser.parse_args()
    try:
        print(json.dumps(ensure(args.check_only), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc), 'action': 'Stop using this skill. Resolve the font/network/permission issue, then rerun; do not substitute another font.'}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
