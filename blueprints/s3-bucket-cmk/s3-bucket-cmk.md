# s3-bucket-cmk

**Provisions** one private S3 bucket: encrypted with the workspace CMK, all four public-access blocks
on, ACLs disabled entirely, plaintext HTTP denied by bucket policy, versioning enabled, and a lifecycle
policy that expires superseded versions and cleans up abandoned multipart uploads. Optionally a second
bucket receiving access logs.

## When to choose it

There is no alternative blueprint to compare against — object storage has no close substitute here.
Choose it for user uploads, generated exports, backups, static assets behind CloudFront, or anything
where the unit is a file rather than a row.

Use `dynamodb-table` instead when the unit is a small structured record you look up by key. The dividing
line is roughly a kilobyte: below it you are paying S3's per-request cost to store almost nothing, and
above about 400 KB DynamoDB refuses the item outright.

## When *not* to

- **As a filesystem.** No partial writes, no append, no rename — a "rename" is a copy plus a delete, and
  a "directory listing" is a paginated API call. Something that expects `open()` and `seek()` wants EFS.
- **For anything latency-critical.** Tens of milliseconds per request, and every request is an HTTP call.
- **For many tiny objects.** A million 1 KB objects costs more in request charges than in storage, and
  each still occupies a 128 KB minimum billable size once transitioned to Standard-IA.
- **As a queue.** Event notifications plus a lifecycle rule looks like one and behaves badly as one.
  Use SQS.

## Cost

`us-east-1`, rounded to what is useful for a decision:

| | |
|---|---|
| Standard storage | **$0.023 per GB-month** |
| Standard-IA | **$0.0125 per GB-month**, plus a retrieval charge and a 128 KB minimum per object |
| PUT / POST / LIST | **$0.005 per thousand** |
| GET | **$0.0004 per thousand** |
| Data out to the internet | **$0.09 per GB** — usually the largest line, and the one nobody forecasts |
| **Idle** | **~$0 for an empty bucket.** No hourly charge at all |
| KMS | $0.03 per 10,000 requests — **but see `BucketKeyEnabled` below** |

10 GB with modest traffic is well under **$1/month**. 500 GB is about **$12**. The number that surprises
people is egress: serving 1 TB to browsers is **$90**, which is why a public-facing bucket usually wants
CloudFront in front of it.

**Two settings here exist purely to stop silent cost growth.** `BucketKeyEnabled: true` caches the KMS
data key at the bucket level — without it every single GET and PUT is a billed KMS API call, and on a
busy bucket the KMS line can exceed the S3 line. And `AbortIncompleteUploadDays` cleans up failed
multipart uploads, whose parts **do not appear in the console object list** but are billed as storage
forever. That is the most common source of S3 spend nobody can account for.

## What it depends on

| Parameter | From |
|---|---|
| `/<workspace>/kms-key-arn` | the workspace CMK |

One parameter. Like `dynamodb-table`, this needs no VPC and no network tier at all, so it stitches into
a workspace that has only run the KMS half of `shared-infra`.

## Five things that bite

**A reader needs `kms:Decrypt`, not just `s3:GetObject`.** This is the single most common failure with a
CMK-encrypted bucket, and the error message names S3 rather than KMS, so it sends you looking in the
wrong policy. Grant both on the consuming role, against `/<workspace>/kms-key-arn`.

**`ListBucket` and `GetObject` are different resources.** The bucket ARN for `ListBucket`, the bucket ARN
`/*` for object actions. A policy with only one of the two produces an `AccessDenied` that looks like a
permissions problem with the action you *did* grant.

**Versioning without lifecycle is an unbounded bill.** Every overwrite keeps the old bytes indefinitely.
`NoncurrentVersionExpirationDays` defaults to 90 for that reason — versioning is on because it is the
only protection against an accidental overwrite, and the expiry is what makes it affordable.

**`DenyWrongEncryption` will reject a caller who asks for SSE-S3 explicitly.** That is deliberate: bucket
default encryption is *not* applied when the request specifies its own, so without this deny an object
can land encrypted with an AWS-owned key while everything appears configured correctly. A client that
sets `x-amz-server-side-encryption: AES256` gets a 403 — the fix is to stop setting it, not to remove the
deny.

**Renaming `BucketPurpose` replaces the bucket, and the replacement fails.** `DeletionPolicy: Retain`
keeps the old bucket, which still holds the globally-unique name, so the create half of the replacement
cannot proceed. Nothing is lost, but the stack rolls back — a rename is a new bucket, a deliberate copy,
and a deliberate delete.

## What this blueprint deliberately leaves out

No Object Lock (it cannot be enabled after creation, and it needs a retention decision no default should
make), no cross-region replication, no event notifications, and no CloudFront origin access control — a
bucket serving a public site is a different blueprint, not a parameter on this one. The access log bucket
uses SSE-S3 rather than the CMK because **S3 log delivery cannot write to a CMK-encrypted bucket**, and
the failure mode is silent: no logs, no error, nowhere to look.
