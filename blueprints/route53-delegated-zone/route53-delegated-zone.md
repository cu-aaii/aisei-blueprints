# route53-delegated-zone

**Provisions** a Route 53 public hosted zone for one subdomain label, and the `NS` record in the
parent zone that makes it resolve.

## When to choose it

**When a workspace needs its own DNS namespace** — its own records, its own certificates, its own
naming — without asking whoever owns the parent zone to add a record every time. That is the whole
trade: one delegation now, then the workspace is autonomous.

**The nearest alternative is not another blueprint — it is not having a zone at all**, and writing
records straight into the parent. Choose that when a workspace has one or two hostnames that will
never change; a zone costs money per month and a shared record costs nothing.

## When *not* to

- **The parent zone is in another AWS account.** This stack writes the `NS` record itself, which means
  it must run where the parent lives. Cross-account delegation needs an assumed role and a custom
  resource, and neither is here. Symptom if you try: `AccessDenied` on the `RecordSet`, *after* the
  zone was created and retained.
- **You need more than one label.** `a.b` against `example.org` needs `b.example.org` to exist and be
  delegated first. This blueprint does not create the intermediate zone, and CloudFormation will not
  tell you — the record is created, the name does not resolve.
- **You want a private zone.** This is a public zone associated with no VPC. A private zone is a
  different resource shape (`VPCs:` instead of a delegation) and would be a separate blueprint.
- **A zone already exists for that name.** Route 53 lets you create a second zone with the same name
  and different name servers. Nothing errors; roughly half of all queries get the wrong answer,
  depending on which NS set the parent points at. **Check `list-hosted-zones-by-name` first.**

## Cost

| | |
|---|---|
| Hosted zone | **$0.50/month**, charged whether or not it answers a single query |
| Queries | $0.40 per million, first billion. Realistically pennies |
| **Idle** | **$0.50/month.** A zone has no idle state — deleting it is the only way to stop paying |

The delegation record itself is free. Twelve abandoned workspace zones cost $72/year, which is the
argument for deleting a zone when a workspace is torn down rather than leaving it.

## What it depends on

Two SSM parameters from the shared contract, both plain `ssm` (never `ssm-secure` — see below):

| Parameter | Used for |
|---|---|
| `/<workspace>/hosted-zone-id` | the parent zone the `NS` record goes into |
| `/<workspace>/hosted-zone-domain` | the parent domain, to build the delegated name |

**Both must describe the same zone.** They are published together by `shared-infra` so they agree by
construction; if you override one, override both. A mismatch creates the `NS` record in a zone that
does not own the parent name — no stack error, and nothing resolves.

## Two things worth knowing

**The zone is `DeletionPolicy: Retain`, deliberately.** Deleting a zone while the parent still points
at its name servers leaves the subdomain *lame-delegated*: queries fail outright rather than falling
back to the parent. Delete the parent's `NS` record first, then the zone.

**Keep `DelegationTtlSeconds` at 300 until you have verified the delegation.** Verify from outside the
account, where a resolver has no privileged view:

```bash
dig +short NS <subdomain>.<parent-domain> @1.1.1.1
# must return the four ns-*.awsdns-* names in this stack's NameServers output
```

A wrong `NS` set with a 172800-second TTL is two days of a name that does not work, and re-pointing it
does not shorten that.
