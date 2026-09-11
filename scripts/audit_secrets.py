#!/usr/bin/env python3
"""The pre-publish audit from README.md, over the working tree and optionally git history.

    python3 scripts/audit_secrets.py            # working tree
    python3 scripts/audit_secrets.py --history  # working tree plus `git log -p --all`

This repo is public and `git log -p` is permanent: a value removed in a later commit is still
published. So this runs in CI on every pull request, not only before the first push.

Exit 1 on any finding that is not on the allowlist below. An allowlist entry is a judgement someone
made once, in writing -- not a way to make the audit quiet.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = {
    'account id': re.compile(r'\b[0-9]{12}\b'),
    'arn': re.compile(r'arn:aws[a-z-]*:'),
    'hosted zone id': re.compile(r'\bZ[A-Z0-9]{10,}\b'),
    'cidr block': re.compile(r'\b[0-9]{1,3}(?:\.[0-9]{1,3}){3}/[0-9]{1,2}\b'),
    'private key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'aws access key id': re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
}

# Each entry is a substring of the offending line and a reason. Read the reason before adding one.
ALLOWED = [
    # apigw-lambda constrains ImageUri to ECR. The twelve digits are a character class in a regex,
    # not an account id.
    ("[0-9]{12}\\.dkr\\.ecr\\.", 'a regex character class in an AllowedPattern, not an account id'),
    # The audit command in README.md and the patterns in this file are the audit itself.
    ("grep -rnE '[0-9]{12}", 'the audit command as documented in README.md'),
    ("'account id': re.compile", 'this file'),
    ("'cidr block': re.compile", 'this file'),
    ("'hosted zone id': re.compile", 'this file'),
    ("'aws access key id': re.compile", 'this file'),
    ("'arn': re.compile", 'this file'),
    # postgres-aurora-serverless and s3-bucket-cmk both close egress to a loopback address, which is
    # the CFN idiom for "no egress" -- a security group cannot have an empty egress list.
    ('127.0.0.1/32', 'the loopback address, the CFN idiom for a security group with no egress'),
    # A public ALB's ingress and an outbound egress rule are both deliberately unconstrained. Neither
    # discloses anything about this account's network -- that is what the pattern is looking for.
    ('0.0.0.0/0', 'anywhere, not a network layout'),
    ('::/0', 'anywhere, not a network layout'),
    # Partition and region are pseudo-parameters. They resolve in whatever account deploys them.
    ('${AWS::Partition}', 'a pseudo-parameter, resolved at deploy time'),
]

SKIP_DIRECTORIES = {'.git', '.venv', 'node_modules', '__pycache__'}


def allowed(line: str) -> str | None:
    for needle, reason in ALLOWED:
        if needle in line:
            return reason
    return None


def scan(label: str, line_number: int, line: str, findings: list[str]) -> None:
    for what, pattern in PATTERNS.items():
        if not pattern.search(line):
            continue
        if allowed(line):
            continue
        findings.append(f'{label}:{line_number}: possible {what}: {line.strip()[:160]}')


def scan_tree(findings: list[str]) -> int:
    scanned = 0
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.relative_to(ROOT).parts):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue  # a binary or unreadable file has nothing greppable in it
        scanned += 1
        label = str(path.relative_to(ROOT))
        for number, line in enumerate(text.splitlines(), start=1):
            scan(label, number, line, findings)
    return scanned


def scan_history(findings: list[str]) -> None:
    try:
        diff = subprocess.run(
            ['git', 'log', '-p', '--all'],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        findings.append(f'git history could not be read: {exc}')
        return

    for number, line in enumerate(diff.splitlines(), start=1):
        # An unchanged context line is already covered by the working-tree pass, and a removal is
        # the case that matters most: the value is gone from HEAD and still in the history.
        if not line.startswith(('+', '-')):
            continue
        scan('git history', number, line[1:], findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='also scan `git log -p --all`')
    args = parser.parse_args()

    findings: list[str] = []
    scanned = scan_tree(findings)
    print(f'{scanned} files scanned', file=sys.stderr)

    if args.history:
        scan_history(findings)
        print('git history scanned', file=sys.stderr)

    for finding in findings:
        print(finding)

    if findings:
        print(
            f'\n{len(findings)} findings. Read each one -- a pattern in an AllowedPattern is not a '
            'leak, and neither is a pseudo-parameter. If it is genuinely fine, add it to ALLOWED '
            'with the reason.',
            file=sys.stderr,
        )
        return 1

    print('clean', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
