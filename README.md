# aisei-blueprints

Versioned, composable CloudFormation building blocks for the Cornell AISEI AI platform. A **blueprint**
is one piece of infrastructure — a Postgres cluster, an ALB and a container, a bucket — not a whole
application. The [Builder Agent](https://github.com/cu-aaii/aisei-agents) reads this repo and stitches
blueprints together into a deployment.

**This repo is public.** It contains no account ids, no ARNs, no CIDR blocks, no hosted zone ids, no
domain names, no workspace names and no secrets — see [Public means audited](#public-means-audited).
Everything account-specific arrives as a parameter or an SSM reference at deploy time, from a private
repo.

## The blueprints

| Blueprint | What it is | Compare with |
|---|---|---|
| [`alb-ecs-service`](blueprints/alb-ecs-service/alb-ecs-service.md) | A container running continuously on Fargate behind its own ALB | `apigw-lambda` |
| [`apigw-lambda`](blueprints/apigw-lambda/apigw-lambda.md) | An HTTP API in front of one Lambda function | `alb-ecs-service` |
| [`dynamodb-table`](blueprints/dynamodb-table/dynamodb-table.md) | One DynamoDB table, CMK-encrypted, optional PITR/TTL/GSI | `postgres-aurora-serverless` |
| [`postgres-aurora-serverless`](blueprints/postgres-aurora-serverless/postgres-aurora-serverless.md) | An Aurora PostgreSQL Serverless v2 cluster in private subnets | `dynamodb-table` |
| [`route53-delegated-zone`](blueprints/route53-delegated-zone/route53-delegated-zone.md) | A subdomain hosted zone plus its `NS` delegation | — |
| [`s3-bucket-cmk`](blueprints/s3-bucket-cmk/s3-bucket-cmk.md) | A private, CMK-encrypted, versioned bucket with a real lifecycle policy | — |

The two pairs are deliberate. Choosing between compute options and between datastores is the decision a
builder actually faces, and each `.md` is written as that comparison rather than as a feature list.

## The shape of one blueprint

```
blueprints/<name>/
├── blueprint.yml       # the manifest: versions[], status, what changed, what replaces on update
├── <name>.md           # when to choose this, when NOT to, cost, cost when idle, dependencies
└── v1/
    ├── template.yml    # the CloudFormation
    └── config.json     # a CloudFormation TemplateConfiguration, all values placeholder
```

Two things about this shape were open questions in the platform's D-76 and are now settled. They are
recorded here because reopening them silently would break anything that pinned a version.

### `config.json` is a CloudFormation `TemplateConfiguration`

```json
{
    "Parameters": { "Application": "", "TableName": "" },
    "Tags": { "Application": "", "Cost Center": "none" }
}
```

Object-shaped, **every value a string**, `Parameters` and `Tags` at the top level. This is the file
CodePipeline consumes as a `TemplateConfiguration` and the shape every config in the platform repo
already uses.

It is **not** the `[{"ParameterKey": …, "ParameterValue": …}]` list that
`aws cloudformation deploy --parameter-overrides` and `cfn-lint --parameter-files` take. Both shapes
could be called "CFN-compatible"; only one is consumed by the pipeline these blueprints deploy through.

The `config.json` in a version directory is a **template to copy**, not a deployable file — its values
are empty strings or `/CHANGE-ME/…` SSM paths. Real values live in the private repo.

### A version is a directory plus a manifest entry

`v1/`, `v2/`, each holding its own `template.yml` and `config.json`, with `blueprint.yml` listing them:

```yaml
name: 'dynamodb-table'
summary: >-
  One DynamoDB table with the workspace CMK, optional point-in-time recovery, TTL and one GSI.
category: 'datastore'
compare_with:
  - 'postgres-aurora-serverless'

versions:
  - version: 'v1'
    status: 'current'          # current | deprecated
    released: '2026-09-11'
    notes: >-
      What this version is, and what it deliberately does not do.
    replaces_resources_on_update: true
    replacement_triggers:
      - 'TableName'
      - 'PartitionKeyName'
```

Not a filename like `postgres-v1.yml`. A filename cannot express a patch to an existing version, and it
has nowhere to put a deprecation marker — which the version-selection rule below actually needs.

**A published version is immutable.** Add `v2/`; never edit `v1/`. Something is deployed from it, and on
a public repo something you do not control may be too.

### Which version a deployment gets

| Situation | Version |
|---|---|
| Building something fresh | the **latest** with `status: current` |
| Updating something already deployed on an older version | **the version it is on** |

The second rule is why `status` and `replaces_resources_on_update` have to be machine-readable: silently
moving a deployed service to a newer blueprint version can replace a database.

`replacement_triggers` names the parameters whose change replaces a resource. For a datastore that is
data loss, so it belongs in the manifest where a tool can warn about it, not only in prose where a
change set is the first thing to mention it.

## Using one

These templates are not deployed from this repo. A private repo holds a filled-in `config.json`, a
pipeline, and the account they go to. To use one by hand:

```bash
# 1. See what it takes and what it costs
cat blueprints/dynamodb-table/dynamodb-table.md
cat blueprints/dynamodb-table/blueprint.yml

# 2. Copy the config template into your private repo and fill it in
cp blueprints/dynamodb-table/v1/config.json /path/to/private/repo/my-table-config.json

# 3. Lint before you deploy anything
cfn-lint blueprints/dynamodb-table/v1/template.yml
```

Every blueprint resolves its account-specific inputs from SSM parameters published by the workspace's
shared infrastructure — `/<workspace>/vpc-id`, `/<workspace>/kms-key-arn` and so on. Each `.md` lists
exactly which, under **What it depends on**, so an incompatible stitch is visible before a deploy rather
than after. All are plain `ssm`, never `ssm-secure`.

## Authoring

Read [`blueprints/route53-delegated-zone/`](blueprints/route53-delegated-zone/) first — it is the
smallest complete example. Then:

1. **`template.yml`** — a `Metadata.Blueprint` block, an `AWS::CloudFormation::Interface` parameter
   grouping, a `Description` on every parameter, and `Rules:` for combinations that cannot be expressed
   as `AllowedValues`. Account-specific values are `AWS::SSM::Parameter::Value<String>` parameters or
   `{{resolve:ssm:/${Application}/…}}` references — never literals.
2. **`config.json`** — every parameter, placeholder values only.
3. **`blueprint.yml`** — one `versions:` entry, `status: current`.
4. **`<name>.md`** — the load-bearing file. Not documentation of the template: the template documents
   itself and a tool can extract every parameter mechanically. This is the *judgement* — when to choose
   it over the named alternative, **when not to**, roughly what it costs per month, what it costs
   **idle**, and which SSM parameters it needs. The "when not to" half is the half that gets skipped and
   the half a reader cannot infer.
5. **`cfn-lint`** must pass clean. Version **1.56 or newer** — older versions ship a stale schema and
   miss real findings.

Two traps worth knowing before you write one, both found the expensive way:

- **`{{resolve:ssm-secure:}}` works on an eleven-property allowlist only.** On any other property the
  template deploys carrying the unresolved literal text *as the secret value* — no stack error, and the
  failure surfaces somewhere unrelated. cfn-lint's `E1027` encodes the allowlist but cannot see inside an
  `Fn::Sub`, so linting stays clean. Use Secrets Manager.
- **A change set does not evaluate `Rules:`.** CloudFormation runs them on create, update and change-set
  *execution* only. A dry run gives you no protection from an invalid parameter combination, so an
  offline mirror of the rules is the only pre-deploy check.

## Public means audited

Auditing is a task, not a formality. `git log -p` on a public repo is permanent: a value removed in a
later commit is still published.

| Never | Why |
|---|---|
| An AWS account id | Names a target; pairs with a role name to make a guessable ARN |
| A CIDR block | Discloses network layout |
| A hosted zone id, or a real domain name | Zone ids are directly queryable |
| Any ARN | Contains the account id and the resource name |
| A workspace name, an app name, or a NetID | Discloses who runs what |
| A secret, token or key — **including a commented-out one** | The most common way one gets published |

CI runs this on every pull request, and it is worth running by hand before pushing a new version:

```bash
python3 scripts/audit_secrets.py --history
```

That is the same grep, with the patterns in one place and an allowlist that has to state its reasons:

```bash
grep -rnE '[0-9]{12}|arn:aws|Z[A-Z0-9]{10,}|[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}' .
git log -p --all | grep -nE '[0-9]{12}|arn:aws'
```

Treat every hit as a finding until you have read it. `arn:${AWS::Partition}:…` inside a template is
fine — it is a pseudo-parameter, and it resolves at deploy time in whatever account deploys it.

## CI

`.github/workflows/ci.yml` runs three steps on every push and pull request. All three run identically
on a laptop — nothing about them needs GitHub, which is the point:

```bash
cfn-lint blueprints/*/v*/template.yml       # every template, cfn-lint 1.56+
python3 scripts/check_structure.py          # the shape this README documents
python3 scripts/audit_secrets.py --history  # the audit above, tree + git log -p --all
```

`check_structure.py` asserts what prose cannot enforce: every `blueprint.yml` parses and names its own
directory; every version it lists has a directory, a `status` of `current` or `deprecated`, a released
date and notes; at least one version is `current`; no version directory exists that the manifest does
not list; `replaces_resources_on_update` and `replacement_triggers` agree with each other; every
`config.json` is valid JSON with exactly `Parameters` and `Tags` and **only string values**; every
template carries its `Metadata.Blueprint` name and version; and no template uses
`{{resolve:ssm-secure:}}`.

`audit_secrets.py` carries a small `ALLOWED` list — `0.0.0.0/0`, `${AWS::Partition}`, the twelve-digit
character class in `apigw-lambda`'s `ImageUri` pattern. Each entry has its reason written next to it.
Adding one is a judgement recorded in the repo, not a way to silence the check.

CI checks out with `fetch-depth: 0`, because the history half of the audit cannot see a shallow clone.
