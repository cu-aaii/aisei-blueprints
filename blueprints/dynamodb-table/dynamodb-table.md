# dynamodb-table

**Provisions** one DynamoDB table, encrypted with the workspace CMK, with optional point-in-time
recovery, a TTL attribute and one global secondary index.

## When to choose it over `postgres-aurora-serverless`

**When you know the access pattern and it is "fetch by key".** DynamoDB is fast and cheap at any scale
for the queries you designed the key schema around, and it is *serverless in the way that matters*:
no subnets, no security group, no engine version, no patch window, no connection pool. Choose it when:

- **Every read is "give me this item" or "give me this partition, in order".** Sessions by session id,
  events by device and timestamp, a lookup table.
- **Idle cost has to be zero.** On-demand billing charges per request. A table nobody touches costs
  storage only — cents. `postgres-aurora-serverless` has a floor even scaled to minimum.
- **Traffic is spiky or unknown.** On-demand absorbs a 10× spike with no capacity planning; Aurora
  scales too, but in seconds rather than instantly, and with a connection limit in the way.
- **The consumer is Lambda.** Thousands of concurrent invocations against a table is normal; the same
  against Postgres exhausts connections, and the fix is RDS Proxy — another component, another cost.

## When *not* to

**When you need queries you have not thought of yet.** This is the real dividing line, and it is worth
being blunt: **an unanticipated query pattern on DynamoDB is a table redesign and a data migration**,
where on Postgres it is a `WHERE` clause and maybe an index. If a human is going to explore this data,
or a report will be defined later, choose `postgres-aurora-serverless`.

Also not, when:

- **Two writes must succeed or fail together across many rows.** `TransactWriteItems` caps at 100
  items and one call. Postgres transactions have no such ceiling.
- **You need joins, aggregates, or `ORDER BY` on a non-key attribute.** There is no query planner.
  Counting rows means scanning the table.
- **The schema must be enforced.** DynamoDB validates the key attributes and nothing else. A bug that
  writes `userID` where the rest of the code reads `userId` produces items that silently do not match.
- **An off-the-shelf tool has to read it** — a BI dashboard, an ORM, `psql`. Almost everything speaks
  Postgres; comparatively little speaks DynamoDB.

## Cost

On-demand, `us-east-1`, and rounded to what is useful for a decision:

| | |
|---|---|
| Writes | **$1.25 per million** (1 KB items) |
| Reads | **$0.25 per million** eventually-consistent |
| Storage | **$0.25 per GB-month** |
| Point-in-time recovery | **$0.20 per GB-month** — roughly the storage price again |
| **Idle** | **~$0.25/month for a 1 GB table.** No request charge for a table nobody queries |

A small application table — 5 GB, a million writes and ten million reads a month, PITR on — lands
around **$6/month**. The same workload on `postgres-aurora-serverless` at 0.5 ACU idle is roughly
**$45/month**. That gap is the strongest argument for this blueprint, and it disappears above roughly
50 GB with heavy scanning.

Provisioned billing is cheaper only above about 20% sustained utilisation, so **leave
`BillingMode` on `PAY_PER_REQUEST`** unless you have measured.

## What it depends on

| Parameter | From |
|---|---|
| `/<workspace>/kms-key-arn` | the workspace CMK, published by `shared-infra` |

Nothing else. No VPC, no subnets, no security group — which is why this blueprint can be stitched into
a workspace that has no private tier.

## Three things that bite

**The key schema is immutable.** `PartitionKeyName`, `PartitionKeyType`, `SortKeyName` and `TableName`
all **replace the table** on change, and the replacement is empty. `DeletionPolicy: Retain` means the
old table survives with your data in it, under the old name, still costing money — so nothing is lost,
but nothing is migrated either. `blueprint.yml` lists these under `replacement_triggers` so
`get_blueprints` can warn before the change set does.

**Adding a sort key later is a replacement.** If items will ever need ordering or a range query inside
a partition, set `SortKeyName` on day one even if unused. It costs nothing empty.

**TTL is not a deadline.** DynamoDB deletes expired items "typically within 48 hours". It is a cost
control. If a retention policy has to be met on a date, delete explicitly and do not use TTL.
