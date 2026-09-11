#!/usr/bin/env python3
"""Generate one JSON Schema per blueprint version, from that version's CloudFormation template.

    python3 scripts/generate_schemas.py            # write blueprints/<name>/v<N>/schema.json
    python3 scripts/generate_schemas.py --check    # exit 1 if any is stale, write nothing

**Why this exists at all.** The Builder Agent validates a rendered config against the blueprint's
template before offering it to anyone, because CloudFormation does not evaluate a template's `Rules:`
block when a change set is created — a config with `UseOidc=true` and five blank endpoints produces a
green pipeline and fails after the merge. That check used to mean parsing CloudFormation YAML at
runtime. The agent is now TypeScript (`aisei-agents` D-84) and reads committed JSON instead, so the
parse moves here: build time, in CI, gated by `--check`.

**What ends up in the JSON.** Everything the agent needs, including the semantics of `Rules:` —
`build_parameters_schema` compiles each Rule into a draft-2020-12 `if`/`then` under
`properties.Parameters.allOf`, with a `$comment` naming the Rule so a validator can say *why* a value
is required rather than "'' is too short". Nothing Python-specific survives into the file, which is
the property that makes a TypeScript consumer possible: `ajv` and `jsonschema` were measured returning
identical verdicts on every config in the central repo.

Three of the six blueprints have a `Rules:` block, and the compiled form is what the agent reads:
`alb-ecs-service` (OIDC needs its five endpoints), `apigw-lambda` (a JWT issuer needs an audience) and
`dynamodb-table` (two — a sort-key type needs a sort key, and an index sort key needs an index
partition key). Each survives as one entry under `properties.Parameters.allOf`, `$comment`-named. The
other three have no cross-parameter constraints, so their schemas are pure parameter shape.

This mirrors `deployment/schema/generate.py` in `cu-aaii/aisei-agents`, whose `cfn_params.py` is
vendored beside this file. That repo's test suite asserts the two produce byte-identical output, so a
divergence fails a gate rather than going unnoticed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from cfn_params import (
    is_required,
    load_template_parameters,
    load_template_rules,
    param_to_json_schema,
    rules_to_json_schema,
)

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINTS = ROOT / 'blueprints'

SCHEMA_FILE_NAME = 'schema.json'

# `Application` is written by manage.py from `--application` at deploy time, for every block in the
# platform, so a blueprint config carrying the key is either redundant or -- the case that costs a
# day -- a stale workspace name copied from another directory. Every blueprint template declares the
# parameter (it is how the stack resolves its SSM contract), and every blueprint's config.json ships
# it as `""`, which this schema then rejects: `"Application": false` is a subschema that matches
# nothing. That is deliberate and matches the central repo -- the committed `""` is the copy-me
# placeholder, and `build` strips the key rather than filling it in.
#
# The name is kept in sync with deployment/schema/generate.py's PARAMETERS_SET_BY_MANAGE_PY. `Branch`
# is in that set too but no blueprint declares it, so listing it here would be dead weight.
PARAMETERS_SET_BY_MANAGE_PY = {'Application'}


def build_parameters_schema(template_path: Path) -> dict:
    cfn_parameters = load_template_parameters(template_path)

    properties: dict[str, object] = {}
    required: list[str] = []

    for name, spec in cfn_parameters.items():
        if name in PARAMETERS_SET_BY_MANAGE_PY:
            properties[name] = False
            continue

        properties[name] = param_to_json_schema(spec)

        if is_required(spec):
            required.append(name)

    schema: dict[str, object] = {
        'type': 'object',
        'additionalProperties': False,
        'required': sorted(required),
        'properties': properties,
    }

    rules = rules_to_json_schema(load_template_rules(template_path), cfn_parameters)
    if rules:
        schema['allOf'] = rules

    return schema


def build_schema(name: str, version: str, template_path: Path) -> dict:
    """The whole config schema for one blueprint version.

    `Tags` is always required, unlike the central repo where it is `shared-infra` only: every
    blueprint's config.json carries the three-key block, because a blueprint stack is a *billable*
    stack and `Cost Center` is how it is attributed.
    """
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': f'{name}/{version}/{SCHEMA_FILE_NAME}',
        'title': f'{name} {version} config',
        'type': 'object',
        'additionalProperties': False,
        'required': ['Parameters', 'Tags'],
        'properties': {
            'Parameters': build_parameters_schema(template_path),
            'Tags': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['Application', 'Resource', 'Cost Center'],
                'properties': {
                    'Application': {'type': 'string'},
                    'Resource': {'type': 'string'},
                    'Cost Center': {'type': 'string'},
                },
            },
        },
    }


def render(schema: dict) -> str:
    # 4-space indent and a trailing newline, matching deployment/schema/*.schema.json exactly -- the
    # drift test compares rendered text, not parsed objects, so formatting is part of the contract.
    return json.dumps(schema, indent=4) + '\n'


def declared_versions(directory: Path) -> list[str]:
    """Version ids from blueprint.yml, in manifest order.

    Read from the manifest rather than globbed off disk, for the same reason check_structure.py
    flags an undeclared directory: a consumer reads the manifest, so a directory nobody declared is
    not part of the catalogue and must not get a schema.
    """
    manifest_path = directory / 'blueprint.yml'
    if not manifest_path.is_file():
        return []

    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    versions = manifest.get('versions')
    if not isinstance(versions, list):
        return []

    return [str(e['version']) for e in versions if isinstance(e, dict) and e.get('version')]


def generate_all() -> dict[Path, tuple[Path, str]]:
    """schema path -> (source template, rendered JSON)."""
    rendered: dict[Path, tuple[Path, str]] = {}

    for directory in sorted(p for p in BLUEPRINTS.iterdir() if p.is_dir()):
        for version in declared_versions(directory):
            template = directory / version / 'template.yml'
            if not template.is_file():
                # check_structure.py is what reports this; generating nothing is the right response
                # here, since there is no template to read.
                continue

            schema = build_schema(directory.name, version, template)
            rendered[directory / version / SCHEMA_FILE_NAME] = (template, render(schema))

    return rendered


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def stale_schemas() -> list[str]:
    stale = []
    for out_path, (template, fresh) in generate_all().items():
        current = out_path.read_text() if out_path.exists() else None
        if current != fresh:
            stale.append(f'{_rel(out_path)} is stale relative to {_rel(template)}')
    return stale


def orphan_schemas() -> list[str]:
    """Schemas on disk for a version the manifest no longer declares.

    A deleted or renamed version leaves its schema behind, and a stale schema is worse than a
    missing one: the agent would validate against a template that is gone.
    """
    expected = set(generate_all())
    return [
        f'{_rel(p)} has no declared version -- delete it'
        for p in sorted(BLUEPRINTS.glob(f'*/*/{SCHEMA_FILE_NAME}'))
        if p not in expected
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='exit 1 if any schema is stale or orphaned; write nothing. This is the CI gate.',
    )
    args = parser.parse_args(argv)

    problems = stale_schemas() + orphan_schemas() if args.check else orphan_schemas()

    if args.check:
        if problems:
            print(f'{len(problems)} schema problem(s):', file=sys.stderr)
            for problem in problems:
                print(f'  {problem}', file=sys.stderr)
            print('\nRun: python3 scripts/generate_schemas.py', file=sys.stderr)
            return 1
        count = len(generate_all())
        print(f'{count} schema(s) are up to date with their templates.')
        return 0

    for problem in problems:
        print(f'warning: {problem}', file=sys.stderr)

    written = 0
    for out_path, (_, fresh) in generate_all().items():
        if not out_path.exists() or out_path.read_text() != fresh:
            out_path.write_text(fresh)
            print(f'wrote {_rel(out_path)}')
            written += 1

    total = len(generate_all())
    print(f'{written} of {total} schema(s) written; {total - written} already current.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
