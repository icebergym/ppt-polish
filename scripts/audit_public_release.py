#!/usr/bin/env python3
"""Audit the text-only public skill directory; project inputs must remain elsewhere."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ALLOWED = {
 '.gitignore', 'README.md', 'SKILL.md', 'version.json', 'agents/openai.yaml',
 'references/brand-layout.md', 'references/story-and-pages.md',
 'references/editing-and-evidence.md', 'references/runtime-and-checks.md',
 'references/evolution.md', 'references/acceptance-cases.md',
 'references/font-setup.md', 'references/misans-source.json',
 'scripts/content_guard.py', 'scripts/version_store.py',
 'scripts/ensure_misans.py', 'scripts/audit_public_release.py',
}
ALLOWED_LINK_HOSTS = {'hyperos.mi.com', 'docs.github.com', 'schemas.openxmlformats.org', 'schemas.microsoft.com'}
IGNORED_LOCAL = {'.git', '.history', '__pycache__', '.DS_Store'}
CHECKS = {
 'credential_pattern': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{24,})'),
 'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'personal_home_path': re.compile('/' + 'Users' + r'/[A-Za-z0-9._-]+/|' + '/' + 'home' + r'/[A-Za-z0-9._-]+/'),
 'email_address': re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b'),
}
URLS = re.compile(r'https?://[^\s<>"\x27`)\]]+')


def inspect_text(path, data, scope):
    findings = []
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return [{'scope': scope, 'path': path, 'reason': 'non_utf8_or_binary_file'}]
    if b'\0' in data:
        findings.append({'scope': scope, 'path': path, 'reason': 'binary_content'})
    for name, pattern in CHECKS.items():
        if pattern.search(text):
            findings.append({'scope': scope, 'path': path, 'reason': name})
    for url in URLS.findall(text):
        host = urlparse(url).hostname
        if host not in ALLOWED_LINK_HOSTS:
            findings.append({'scope': scope, 'path': path, 'reason': 'unreviewed_external_link'})
    return findings


def audit(root, history=False):
    findings = []
    found = set()
    for file in sorted(root.rglob('*')):
        rel = file.relative_to(root)
        if any(part in IGNORED_LOCAL for part in rel.parts):
            continue
        if file.is_symlink():
            findings.append({'path': str(rel), 'reason': 'symlink_not_allowed'})
        elif file.is_file():
            path = rel.as_posix()
            found.add(path)
            if path not in ALLOWED:
                findings.append({'path': path, 'reason': 'file_not_allowlisted'})
            findings += inspect_text(path, file.read_bytes(), 'working-tree')
    for missing in sorted(ALLOWED - found):
        findings.append({'path': missing, 'reason': 'required_public_file_missing'})
    commits = 0
    if history:
        def git(*args):
            return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
        for commit in git('rev-list', '--all').decode().splitlines():
            commits += 1
            message = git('show', '-s', '--format=%B', commit)
            findings += inspect_text('(commit-message)', message, commit)
            for row in git('ls-tree', '-r', '-z', commit).split(b'\0'):
                if not row:
                    continue
                metadata, rawpath = row.split(b'\t', 1)
                mode, kind, oid = metadata.decode().split()
                path = rawpath.decode('utf-8')
                if path not in ALLOWED or mode not in ('100644', '100755') or kind != 'blob':
                    findings.append({'scope': commit, 'path': path, 'reason': 'unapproved_historical_file'})
                else:
                    findings += inspect_text(path, git('cat-file', 'blob', oid), commit)
    return {'status': 'passed' if not findings else 'blocked', 'files': len(found), 'commits_checked': commits, 'findings': findings, 'limit': 'Pattern checks do not identify all private content. Human review of text, metadata and newly allowed files is still required.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--history', action='store_true', help='Also inspect every commit reachable from local Git refs')
    args = parser.parse_args()
    try:
        report = audit(Path(args.root).resolve(), args.history)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['status'] == 'passed' else 1
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
