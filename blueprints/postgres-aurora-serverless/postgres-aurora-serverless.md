# postgres-aurora-serverless

**Provisions** an Aurora PostgreSQL Serverless v2 cluster in the workspace private subnets — one
writer, an optional reader, encrypted with the workspace CMK, with the master password generated into
Secrets Manager and a security group that starts with **no ingress rule at all**.

## When to choose it over `dynamodb-table`

**When you do not yet know every query.** That is the whole argument, and it is usually decisive.
Postgres lets a query you did not anticipate be a `WHERE` clause and maybe an index; on DynamoDB the
same query can be a key-schema change, which means a new table and a data migration. Choose it when:

- **A human will explore the data**, or a report will be specified after launch. `psql`, a BI tool and
  every ORM already speak Postgres.
- **You need joins, aggregates, `ORDER BY` on any column, or `COUNT(*)`.** There is a query planner
  here. DynamoDB has none — counting means scanning.
- **Writes must be transactional across many rows.** No 100-item ceiling, no per-call limit.
- **The schema should be enforced.** A `NOT NULL` or a foreign key turns a class of application bug
  into a rejected write, at the one layer every code path goes through.
- **You want a familiar operational surface.** `EXPLAIN`, `pg_stat_statements`, an index you can add
  online. Slow-query debugging on DynamoDB is mostly re-reading your key design.

## When *not* to

**When idle cost has to be near zero.** At the default 0.5 ACU minimum this cluster costs roughly
**$45/month doing nothing at all**, where an idle `dynamodb-table` costs cents. For a low-traffic
internal tool that difference is most of the bill.

Also not, when:

- **The consumer is Lambda at high concurrency.** A thousand concurrent invocations exhaust the
  connection limit; the fix is RDS Proxy — another component, another cost, another failure mode.
  DynamoDB has no connection concept.
- **Traffic is genuinely spiky.** Serverless v2 scales in seconds, not instantly, and the doubling
  behaviour means a sharp 10× spike sees some errors on the way up.
- **You need it up in a workspace with no private tier.** This blueprint needs a VPC, two subnets in
  two availability zones and a security group. `dynamodb-table` needs a KMS key and nothing else.
- **Every access is by primary key at very high volume.** Paying for a query planner you never use.

## Cost

`us-east-1`, Aurora Standard storage, rounded to what is useful for a decision:

| | |
|---|---|
| Compute | **$0.12 per ACU-hour** |
| **Idle at `MinCapacityAcu: 0.5`** | **~$44/month** — this is the floor, and it is the number to argue about |
| Idle at `MinCapacityAcu: 0` | **$0** while paused, but the first connection after a pause waits ~15 seconds |
| Ceiling at `MaxCapacityAcu: 4` | **~$350/month** if it ever sat pinned at maximum |
| Storage | **$0.10 per GB-month** |
| I/O | **$0.20 per million requests** |
| Reader instance | **doubles compute** |
| Backups | free up to the cluster size, then $0.021 per GB-month |

The comparison in `dynamodb-table.md` is the same workload both ways: a small application table with a
million writes and ten million reads a month lands near **$6/month** on DynamoDB and near
**$45/month** here. Above roughly 50 GB with heavy scanning, that reverses.

`MaxCapacityAcu` is the only thing between a runaway query and a large bill. Leave it low and raise it
on evidence.

## What it depends on

| Parameter | From |
|---|---|
| `/<workspace>/vpc-id` | the workspace VPC, published by `shared-infra` |
| `/<workspace>/subnets-core-private` | the private subnet list — **must span two availability zones** |
| `/<workspace>/kms-key-arn` | the workspace CMK, encrypting both the volume and the secret |

All three are plain `ssm`, resolved at parameter time. The subnet list is the one that fails in a
confusing way: Aurora rejects a single-AZ subnet group with an error naming the subnet group rather
than the missing zone.

## Four things that bite

**Nothing can connect until you say so.** The security group has no ingress rule. That is deliberate —
an open CIDR in a blueprint is invisible at review time and permanent in practice. The consuming
service adds an ingress rule referencing its own security group, using the `SecurityGroupId` output.

**The password is never in a file.** `GenerateSecretString` creates it inside Secrets Manager and the
cluster reads it back with `{{resolve:secretsmanager:…}}`. Deliver it to a task as an ECS `Secrets`
entry — never resolve it into an environment variable in a template, where it lands in the task
definition in plaintext. Note that `{{resolve:ssm-secure:}}` would **not** work in its place:
that resolver runs on an 11-property allowlist and `MasterUserPassword` is not on it, so the reference
would deploy as literal text and every connection would fail authentication.

**Use the cluster endpoints, not the instance ones.** `WriterEndpoint` follows a failover;
`ReaderEndpoint` is valid even with no reader instance — it resolves to the writer, so a read-only
consumer needs no config change when a reader is added later.

**`DeletionProtection` is on and the cluster snapshots on delete.** Turning protection off is a
separate stack update before any teardown can proceed, which is the point. `DeletionPolicy: Snapshot`
means a stack delete leaves a final snapshot behind, and that snapshot keeps costing storage until
someone deletes it deliberately.
