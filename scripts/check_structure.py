#!/usr/bin/env python3
"""Check that every blueprint has the shape README.md documents.

Run from the repo root:

    python3 scripts/check_structure.py

Exits non-zero and prints one line per finding. Deliberately has no dependency
beyond PyYAML, so it runs the same in CI and on a laptop.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINTS = ROOT / 'blueprints'

VALID_STATUS = {'current', 'deprecated'}


def check_blueprint(directory: Path, findings: list[str]) -> None:
    name = directory.name
    say = lambda msg: findings.append(f'{name}: {msg}')  # noqa: E731

    manifest_path = directory / 'blueprint.yml'
    if not manifest_path.is_file():
        say('no blueprint.yml')
        return

    try:
        manifest = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as exc:
        say(f'blueprint.yml does not parse: {exc}')
        return

    if not isinstance(manifest, dict):
        say('blueprint.yml is not a mapping')
        return

    if manifest.get('name') != name:
        say(f'blueprint.yml name is {manifest.get("name")!r}, expected {name!r}')

    for required in ('summary', 'category'):
        if not manifest.get(required):
            say(f'blueprint.yml has no {required}')

    # The .md is the load-bearing file, so its absence is an error rather than a note.
    if not (directory / f'{name}.md').is_file():
        say(f'no {name}.md')

    versions = manifest.get('versions')
    if not isinstance(versions, list) or not versions:
        say('blueprint.yml lists no versions')
        return

    declared = []
    for entry in versions:
        if not isinstance(entry, dict):
            say(f'version entry is not a mapping: {entry!r}')
            continue

        version = entry.get('version')
        if not version:
            say('version entry has no version')
            continue
        declared.append(version)

        status = entry.get('status')
        if status not in VALID_STATUS:
            say(f'{version}: status is {status!r}, expected one of {sorted(VALID_STATUS)}')

        if not entry.get('released'):
            say(f'{version}: no released date')
        if not entry.get('notes'):
            say(f'{version}: no notes')

        # replacement_triggers without replaces_resources_on_update is contradictory, and the
        # reverse is a promise with nothing behind it -- a consumer cannot warn about anything.
        replaces = entry.get('replaces_resources_on_update')
        triggers = entry.get('replacement_triggers') or []
        if replaces is None:
            say(f'{version}: no replaces_resources_on_update')
        elif replaces and not triggers:
            say(f'{version}: replaces_resources_on_update is true but no replacement_triggers')
        elif not replaces and triggers:
            say(f'{version}: has replacement_triggers but replaces_resources_on_update is false')

        version_dir = directory / str(version)
        if not version_dir.is_dir():
            say(f'{version}: no {version}/ directory')
            continue

        check_version(version_dir, name, str(version), say)

    if 'current' not in [e.get('status') for e in versions if isinstance(e, dict)]:
        say('no version has status current')

    # A directory nobody declared is either an unpublished draft or a manifest omission. Either way
    # a consumer reading the manifest will not see it.
    on_disk = {p.name for p in directory.iterdir() if p.is_dir()}
    for orphan in sorted(on_disk - set(map(str, declared))):
        say(f'{orphan}/ exists on disk but is not in blueprint.yml')


def check_version(version_dir: Path, name: str, version: str, say) -> None:
    template = version_dir / 'template.yml'
    config = version_dir / 'config.json'

    if not template.is_file():
        say(f'{version}: no template.yml')
    else:
        try:
            # CloudFormation short forms (!Ref, !Sub) are not standard YAML tags, so the template
            # is read as text here. cfn-lint is what actually parses it.
            text = template.read_text()
        except OSError as exc:
            say(f'{version}: template.yml unreadable: {exc}')
            text = ''

        if 'AWSTemplateFormatVersion' not in text:
            say(f'{version}: template.yml has no AWSTemplateFormatVersion')
        if f"Name: '{name}'" not in text:
            say(f'{version}: template.yml has no Metadata.Blueprint.Name of {name!r}')
        if f"Version: '{version}'" not in text:
            say(f'{version}: template.yml has no Metadata.Blueprint.Version of {version!r}')
        # Only actual usage, and only outside comments -- several templates carry a comment
        # explaining why they use Secrets Manager instead, and that comment is the point.
        for number, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith('#'):
                continue
            if 'resolve:ssm-secure' in line:
                say(
                    f'{version}: template.yml line {number} uses ssm-secure, which silently '
                    'fails on all but eleven properties'
                )

    if not config.is_file():
        say(f'{version}: no config.json')
        return

    try:
        parsed = json.loads(config.read_text())
    except json.JSONDecodeError as exc:
        say(f'{version}: config.json does not parse: {exc}')
        return

    if set(parsed) != {'Parameters', 'Tags'}:
        say(f'{version}: config.json top-level keys are {sorted(parsed)}, expected Parameters and Tags')
        return

    for section in ('Parameters', 'Tags'):
        block = parsed[section]
        if not isinstance(block, dict):
            say(f'{version}: config.json {section} is not an object')
            continue
        for key, value in block.items():
            # A CFN TemplateConfiguration is strings all the way down -- a number or a bool here is
            # rejected by CodePipeline, not coerced.
            if not isinstance(value, str):
                say(f'{version}: config.json {section}.{key} is {type(value).__name__}, expected string')


def main() -> int:
    if not BLUEPRINTS.is_dir():
        print('no blueprints/ directory', file=sys.stderr)
        return 1

    findings: list[str] = []
    directories = sorted(p for p in BLUEPRINTS.iterdir() if p.is_dir())

    if not directories:
        print('blueprints/ is empty', file=sys.stderr)
        return 1

    for directory in directories:
        check_blueprint(directory, findings)

    for finding in findings:
        print(finding)

    print(
        f'\n{len(directories)} blueprints checked, {len(findings)} findings',
        file=sys.stderr,
    )
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
