# alb-ecs-service

**Provisions** one container running continuously on Fargate, behind its own internet-facing
Application Load Balancer: two security groups, an HTTPS listener on the workspace wildcard
certificate, an HTTP→HTTPS redirect, a WAF association, an A record (and AAAA when dualstack), a log
group, two IAM roles and the ECS service itself.

## When to choose it over `apigw-lambda`

**When the thing you are deploying is a server.** An image that listens on a port, holds state in
memory, keeps a connection pool, streams a response, or takes longer than fifteen minutes to finish a
request. Choose it when:

- **You have a container already.** Anything that runs under `docker run` runs here unchanged. No
  handler signature, no packaging step, no runtime version to track.
- **Requests are steady.** Above roughly a million requests a month, always-on Fargate is usually
  cheaper than per-invocation billing — and the cost is flat and predictable rather than a function of
  traffic.
- **Cold start is unacceptable.** The task is already warm. There is no first-request penalty ever.
- **You hold a connection pool.** One task, one pool. Lambda's answer to the same problem is RDS Proxy.
- **A response streams, or a request runs long.** No 15-minute ceiling, no 6 MB payload limit.
- **The framework expects to be a server.** Most of them do. Express, FastAPI, Spring, Rails and a
  static file server all work here without an adapter.

## When *not* to

**When it is idle most of the time.** This blueprint's floor is an ALB you pay for by the hour whether
or not anyone visits: about **$23/month before a single request**, plus the task. An internal tool used
twice a week costs the same as one used constantly. `apigw-lambda` costs nothing idle.

Also not, when:

- **The work is event-driven.** A queue consumer, an S3-triggered job, a scheduled task. There is no
  HTTP request to load-balance, so most of what this blueprint provisions is waste.
- **Traffic is extremely spiky.** ECS scales in minutes and this template has no autoscaling at all —
  `DesiredCount` is a fixed number. Lambda absorbs a 100× spike with no configuration.
- **You want ten of them.** Ten services means ten ALBs and roughly **$230/month in load balancers
  alone**. That is the question D-69 leaves open: a shared ALB with host-based rules costs one ALB for
  all of them, at the price of a shared failure domain and rule-priority coordination. Until that is
  settled, count the ALBs before choosing this ten times.

## Cost

`us-east-1`, rounded to what is useful for a decision:

| | |
|---|---|
| ALB | **~$16/month** fixed, plus LCU charges — call it **~$23/month** at low traffic |
| Fargate, 1 vCPU / 2 GB, always on | **~$36/month** |
| Fargate, 0.25 vCPU / 0.5 GB | **~$9/month** |
| **Idle** | **~$59/month at the defaults.** There is no idle state — you pay for the ALB and the task around the clock |
| Two tasks | add another **~$36/month** |
| Logs | $0.50 per GB ingested, $0.03 per GB-month stored |

A typical service at the defaults is about **$60/month regardless of traffic**. `apigw-lambda` doing
100,000 requests a month is under **$1**. That difference is the whole comparison, and it reverses
somewhere around a million requests a month — or immediately, if the workload needs anything on the
"when to choose it" list.

`TaskCpu: 256` / `TaskMemory: 512` cuts the compute part by three quarters and is enough for a great
many small services. The ALB cost does not move.

## What it depends on

| Parameter | From |
|---|---|
| `/<workspace>/vpc-id` | the workspace VPC |
| `/<workspace>/subnet-core-public-a`, `-b` | the ALB's two public subnets |
| `/<workspace>/subnet-core-private-a`, `-b` | where the task runs |
| `/<workspace>/ecs-cluster-name` | the shared Fargate cluster |
| `/<workspace>/domain-wildcard-certificate-arn` | the HTTPS listener certificate |
| `/<workspace>/hosted-zone-id`, `/hosted-zone-domain` | the A record and the host-header rule |
| `/<workspace>/basic-waf-arn` | the WAF association |
| `/<workspace>/kms-key-arn` | log group encryption |

Nine parameters — the most of any blueprint here, and the reason this one cannot be stitched into a
workspace that has not run `shared-infra` first. All are plain `ssm`; none is `ssm-secure`.

## Five things that bite

**The health check path must answer before authentication.** The ALB health check carries no OIDC
session and no cookie. If `ServiceHealthCheckPath` sits behind the application's own login redirect,
the target never becomes healthy, the deployment circuit breaker trips, and the error you see is a
rollback rather than a 302.

**OIDC here is authentication, not authorization.** The listener rule proves who the caller is. It says
nothing about whether that person should be allowed in — with an Entra app registration that has
assignment-required off, *anyone in the tenant* passes. Real access control is the application's job,
and this is a mistake already made in production elsewhere in the fleet.

**The client secret comes from Secrets Manager, not `ssm-secure`.** `{{resolve:ssm-secure:}}` works on
an eleven-property allowlist and `ListenerRule` is not on it. A wrong reference deploys as literal
text, the stack reaches `CREATE_COMPLETE`, and every login fails at the token exchange with
`invalid_client`. cfn-lint's `E1027` encodes the same allowlist but cannot see inside `Fn::Sub`, so it
will not warn you. The secret must exist, by hand, at
`<workspace>-<service>-<environment>-oidc-client-secret`, holding a JSON object with a `client_secret`
key, *before* the first deploy.

**`RequiresCompatibilities: [FARGATE]` is not optional.** Omit it and ECS defaults the list to EC2; a
Fargate-only cluster then refuses to place the task with "task definition does not support launch type
FARGATE", the circuit breaker rolls the stack back, and cfn-lint says nothing.

**Pin the image by digest.** `ServiceImageUri` accepts a tag, but a moving tag means the stack no
longer describes what is running, and a rollback has nothing definite to roll back to. Use
`repo@sha256:…`.

## What this blueprint deliberately leaves out

The platform's own `services/_template/service-stack.yml` is this blueprint plus an EFS access-point
pair, an S3 configuration bucket with an environment file, an alternate-hostname certificate, an ALB
access-log bucket with Object Lock, and Service Connect. Those are the platform's conventions, not
properties of "a container behind a load balancer" — a blueprint that included them would be
unusable outside it. There is also **no autoscaling target** here: `DesiredCount` is fixed. Add
`AWS::ApplicationAutoScaling::ScalableTarget` when there is a measured reason to.
