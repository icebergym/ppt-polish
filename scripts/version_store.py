#!/usr/bin/env python3
"""Immutable local skill snapshots; restore into a new directory, never the active skill."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MANIFEST = '_snapshot_manifest.json'
EXCLUDED = {'.history', '.git', '__pycache__', '.DS_Store'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    files = {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if any(x in EXCLUDED for x in rel.parts):
            continue
        if p.is_symlink():
            raise ValueError('Symlinks are not portable snapshot assets: ' + str(rel))
        if p.is_file():
            name = rel.as_posix()
            if name == MANIFEST:
                raise ValueError('Reserved snapshot name: ' + name)
            data = p.read_bytes()
            files[name] = {'sha256': digest(data), 'bytes': len(data)}
    return files


def snapshot(root, note):
    root = Path(root).resolve()
    meta = json.loads((root / 'version.json').read_text(encoding='utf-8'))
    version = meta['version']
    if meta.get('skill') != 'ppt-polish' or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
        raise ValueError('Unexpected skill or invalid version')
    if not (root / 'SKILL.md').is_file():
        raise ValueError('Missing SKILL.md')
    files = inventory(root)
    store = root / '.history' / 'versions'
    store.mkdir(parents=True, exist_ok=True)
    revision = meta.get('snapshot_revision', '')
    if not isinstance(revision, str) or (revision and not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}', revision)):
        raise ValueError('Invalid snapshot revision')
    target = store / (version + ('-' + revision if revision else '') + '.zip')
    manifest = {'schema': 'ppt-skill-snapshot-v1', 'skill': meta['skill'], 'version': version, 'created_at': datetime.now(timezone.utc).isoformat(), 'note': note, 'files': files}
    if revision:
        manifest['snapshot_revision'] = revision
    if target.exists():
        previous, _ = validate_archive(target)
        if previous['files'] == files:
            return {'status': 'already_snapshotted', 'path': str(target), 'version': version, 'file_count': len(files)}
        raise ValueError('This snapshot already has different contents; use a new confirmed version or an explicitly approved finalization revision.')
    temporary = None
    try:
        fd, tempname = tempfile.mkstemp(prefix='.snapshot-', suffix='.zip', dir=store)
        os.close(fd)
        temporary = Path(tempname)
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, info in files.items():
                data = (root / name).read_bytes()
                if digest(data) != info['sha256']:
                    raise ValueError('Source changed during snapshot: ' + name)
                z.writestr(name, data)
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        validate_archive(temporary)
        # Hard link gives a no-overwrite publication on the same filesystem.
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'status': 'created', 'path': str(target), 'version': version, 'file_count': len(files)}


def validate_archive(archive):
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive members')
        for i in infos:
            p = PurePosixPath(i.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in i.filename or not p.parts or ':' in p.parts[0] or p.as_posix() != i.filename or i.is_dir():
                raise ValueError('Unsafe archive path: ' + i.filename)
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ValueError('Symlink archive member: ' + i.filename)
            if any(x in EXCLUDED for x in p.parts):
                raise ValueError('Unexpected nested history or metadata')
        if sum(i.file_size for i in infos) > 512 * 1024 * 1024:
            raise ValueError('Archive exceeds the 512 MiB snapshot limit')
        m = json.loads(z.read(MANIFEST))
        if m.get('schema') != 'ppt-skill-snapshot-v1' or m.get('skill') != 'ppt-polish':
            raise ValueError('Unsupported snapshot')
        if set(names) != set(m['files']) | {MANIFEST}:
            raise ValueError('Archive and manifest differ')
        data = {}
        for name, info in m['files'].items():
            content = z.read(name)
            if len(content) != info['bytes'] or digest(content) != info['sha256']:
                raise ValueError('Snapshot integrity failure: ' + name)
            data[name] = content
        meta = json.loads(data['version.json'])
        if meta.get('version') != m['version'] or meta.get('skill') != m['skill']:
            raise ValueError('Snapshot version metadata mismatch')
        if meta.get('snapshot_revision', '') != m.get('snapshot_revision', ''):
            raise ValueError('Snapshot revision metadata mismatch')
    return m, data


def restore(archive, destination):
    m, data = validate_archive(archive)
    dest = Path(destination).absolute()
    if dest.exists() or dest.is_symlink():
        raise ValueError('Restore destination must not exist; the active skill is never overwritten.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.ppt-restore-', dir=dest.parent))
    try:
        for name, content in data.items():
            p = temp / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        # Reserve the destination, then move the verified files without touching other directories.
        dest.mkdir(exist_ok=False)
        for p in temp.iterdir():
            shutil.move(str(p), str(dest / p.name))
    except Exception:
        # Only the private staging directory is cleaned; an interrupted destination is left visible.
        raise
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return {'status': 'restored_to_new_directory', 'path': str(dest), 'version': m['version'], 'file_count': len(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    sp = subs.add_parser('snapshot')
    sp.add_argument('root')
    sp.add_argument('--note', required=True)
    rp = subs.add_parser('restore')
    rp.add_argument('archive')
    rp.add_argument('destination')
    vp = subs.add_parser('verify')
    vp.add_argument('archive')
    args = parser.parse_args()
    try:
        if args.command == 'snapshot':
            result = snapshot(args.root, args.note)
        elif args.command == 'restore':
            result = restore(args.archive, args.destination)
        else:
            m, data = validate_archive(args.archive)
            result = {'status': 'verified', 'version': m['version'], 'file_count': len(data)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
