# apigw-lambda

**Provisions** an HTTP API with one catch-all route proxying to one Lambda function: the function and
its role, a log group per side, throttling, an optional JWT authorizer, a custom domain on the
workspace wildcard certificate, and an A record. Optionally attached to the workspace VPC.

## When to choose it over `alb-ecs-service`

**When idle is the normal state.** This is the argument, and for internal tooling it is usually the
right one. Nothing runs between requests and nothing is billed — an API used twice a week costs
pennies, where `alb-ecs-service` costs about **$60/month** for the same thing. Choose it when:

- **Traffic is low, bursty or unpredictable.** Under roughly a million requests a month this is
  dramatically cheaper. Above it, the comparison flips.
- **A spike must be absorbed instantly.** Lambda scales to a thousand concurrent executions with no
  configuration. ECS takes minutes, and this repo's `alb-ecs-service` has no autoscaling at all.
- **The work is a webhook, a callback or a small JSON API.** Short, stateless, one request one answer.
- **You want fewer moving parts.** No cluster, no target group, no health check to get wrong, no
  deployment circuit breaker, no load balancer to pay for.

## When *not* to

**When the request takes more than 29 seconds.** API Gateway's integration timeout is a hard ceiling
and no parameter here raises it. `TimeoutSeconds` can go to 900, but over HTTP the caller gets a 504
at 29 regardless. Long work belongs behind a queue, or on `alb-ecs-service`.

Also not, when:

- **The response streams.** No server-sent events, no chunked responses, no long-polling. This is the
  one that surprises people — an LLM chat endpoint that streams tokens cannot live here.
- **Cold start matters.** First request after idle is hundreds of milliseconds to several seconds
  depending on image size. Provisioned concurrency fixes it and costs money continuously, at which
  point `alb-ecs-service` is the simpler purchase.
- **You hold a connection pool.** Each concurrent execution is a separate process with its own
  connections; 50 concurrent invocations against `postgres-aurora-serverless` is 50 pools. The fix is
  RDS Proxy — another component. One ECS task has one pool.
- **Traffic is steady and high.** Per-request billing on a constant load costs more than a task that
  is always on. Do the arithmetic at your actual request count.
- **You need WAF.** HTTP APIs cannot take a WAF association. A REST API can, and so can an ALB. If a
  WAF is a requirement, this blueprint is the wrong one.
- **The framework wants to be a server.** Adapters exist and mostly work, but "mostly" is doing real
  work in that sentence. A container that already runs is less trouble.

## Cost

`us-east-1`, arm64, rounded to what is useful for a decision:

| | |
|---|---|
| HTTP API requests | **$1.00 per million** (first 300M/month) |
| Lambda requests | **$0.20 per million** |
| Lambda duration, arm64 | **$0.0000133 per GB-second** |
| **Idle** | **$0.** No load balancer, no reserved capacity, nothing hourly |
| Custom domain | free |
| Logs | $0.50 per GB ingested |

100,000 requests a month at 512 MB and 200 ms is **under $1/month** all in. A million requests at the
same shape is about **$3**. Ten million is about **$30**, which is where `alb-ecs-service` at ~$60 flat
starts looking reasonable — and past that, cheaper.

`MemorySizeMb` is a CPU dial as well: a function is often *cheaper* at 1024 MB than 512 because it
finishes in less than half the time. Measure rather than economise.

**`ReservedConcurrency` defaults to 10, deliberately.** Left unreserved, one function can consume the
account's entire 1000-concurrency pool and starve every other function in it — and per-request billing
has no natural ceiling, so a retry loop in someone else's code is an unbounded bill. `ThrottlingRateLimit`
is the second ceiling, at the API.

## What it depends on

| Parameter | From |
|---|---|
| `/<workspace>/kms-key-arn` | log-group and environment-variable encryption |
| `/<workspace>/domain-wildcard-certificate-arn` | the custom domain |
| `/<workspace>/hosted-zone-id`, `/hosted-zone-domain` | the A record and the hostname |
| `/<workspace>/vpc-id`, `/subnet-core-private-a`, `-b` | **only when `AttachToVpc` is true** |

Four parameters at rest, seven in a VPC — against `alb-ecs-service`'s nine, which is part of why this
one deploys into a thinner workspace.

## Five things that bite

**`JwtIssuer` empty means the API is fully public.** No authorizer, every route open to the internet.
That is correct for a public webhook receiver and a mistake everywhere else. There is no
"authenticated by default" here the way the ALB blueprint's 401 default action gives you.

**The `execute-api` endpoint stays reachable.** The custom domain is an additional front door, not a
replacement — `https://<api-id>.execute-api.<region>.amazonaws.com` answers too, and it bypasses
nothing except your DNS. It is only safe because the authorizer is attached to the route rather than to
the domain. HTTP APIs have no resource policy to lock it down with; that is a REST API feature.

**`AttachToVpc` costs more than it looks.** An ENI per concurrent execution, slower cold starts, and —
the one that bites — **no route to the internet** unless the private subnets have a NAT gateway. A
function that calls an external API works fine outside the VPC and silently times out inside it. Turn
it on only to reach something private, like `postgres-aurora-serverless`.

**Lambda resolves the image tag once.** At deploy, and never again. Pushing a new image to the same tag
changes nothing until the next stack update — so a "deploy" that appears to do nothing usually did
exactly that. Pin by digest and the confusion goes away.

**The log group is created here on purpose.** Left to Lambda, it appears on first invocation with **no
retention** and never gets any, accumulating forever. The `DependsOn` matters: without it the function
can be created first and win the race.

## What this blueprint deliberately leaves out

**No zip package.** A zip needs a `Runtime` parameter, and runtimes expire — cfn-lint already reports
`nodejs20.x` as deprecated, with creation disabled in early 2027. Every such list needs revisiting on
AWS's schedule rather than yours, and this blueprint would go stale in place. A container image has no
expiry and the platform's build pipeline already produces one.

Also absent: provisioned concurrency, a Lambda alias or version (so no gradual traffic shifting), a
dead-letter queue, and any non-HTTP invoker. Explicit per-path routes would need adding by hand if two
paths ever need different authorizers — `$default` catches everything, and the function routes.
