# Avoiding Bans, Blocks, and Restrictions on AI Horde

Analysis of `refs/AI-Horde` server source code — covering every mechanism that can
ban, block, freeze, rate-limit, or restrict access for IPs, API keys, client agents,
and workers.

---

## 1. IP Address Bans

### 1.1 IP Timeout (Fibonacci escalation)

**Source:** `horde/countermeasures.py` — `report_suspicion()`, `set_timeout()`

Each time an IP is reported suspicious the timeout grows by a Fibonacci sequence
`(n + n + 1) * 3` seconds:

| Suspicion count | Timeout |
|---|---|
| 1 | 3 s |
| 2 | 9 s |
| 3 | 21 s |
| 4 | 45 s |
| 10 | ~5 min |
| 20 | ~4 days |

Suspicion score resets after **24 hours** (1 hour for whitelisted service IPs).
The timeout check returns HTTP **403 `TimeoutIP`**.

**What triggers IP suspicion:**
- Submitting a prompt that matches the corruption/abuse filters (see §5)
- Worker running on a VPN/proxy IP without the VPN flag on the account
- Proxy service that routes too many bad prompts through it

### 1.2 IP Block Timeout (CIDR subnet ban)

**Source:** `horde/countermeasures.py` — `set_block_timeout()` / `retrieve_block_timeout()`

Admins can ban entire subnets (CIDR notation, e.g. `192.0.2.0/24`) for a specified
number of minutes. The ban is stored in Redis as `ipblock_<CIDR>`.
Returns HTTP **403 `TimeoutIP`** for any IP within the range.

### 1.3 VPN / Proxy IP detection

**Source:** `horde/countermeasures.py` — `is_ip_safe()`

An external IP-checker service is queried for each new IP. If the probability of the
IP being a VPN/proxy exceeds **0.93** the request is rejected with HTTP **403
`UnsafeIP`**. The result is cached in Redis for **6 hours**.

**Exemptions (bypass the check entirely):**
- Account has the `trusted` role
- Account has the `vpn` flag
- Account is a Patron
- IP is in the server's `WHITELISTED_VPN_IPS` list

---

## 2. API Key / Account Restrictions

### 2.1 Flagged account

**Source:** `horde/classes/base/user.py` — `flagged` property

Set permanently by moderators via the `UserRole` table.

Effects:
- Workers owned by this account cannot pop jobs → HTTP **403 `WorkerFlaggedMaintenance`**
- Kudos transfer blocked
- Kudos awards blocked
- NSFW model prompts replaced / blocked even if the prompt passes normal checks

### 2.2 Deleted account

**Source:** `horde/classes/base/user.py` — `deleted` property

All generation requests return HTTP **403 `DeletedUser`**.
Workers owned by this account cannot pop jobs.

### 2.3 User suspicion accumulation

**Source:** `horde/classes/base/user.py` — `is_suspicious()`, threshold = **5**

Suspicion reasons that add to the counter:
- `CORRUPT_PROMPT` — prompt matched the abuse filter
- `USERNAME_LONG` — username exceeds allowed length
- `USERNAME_PROFANITY` — username contains prohibited words

Trusted users are never flagged as suspicious.
At threshold the account is not immediately hard-blocked but workers are
automatically paused and moderators are alerted.

### 2.4 Insufficient kudos (high-demand requests)

**Source:** `horde/apis/v2/stable.py` — `KudosUpfront`

During high-demand periods, expensive requests (large resolution, >50 steps, etc.)
require the account to hold enough kudos upfront or they are rejected with HTTP
**403 `KudosUpfront`**.

---

## 3. Client Agent Banning

**Source:** `horde/apis/v2/stable.py` lines 212–218

The server hard-blocks any request whose `Client-Agent` header exactly matches a
built-in denylist. Currently the only blocked value is the **default placeholder**
that Horde's own documentation shows as an example:

```
My-Project:v0.0.1:My-Contact
```

Any client that ships or leaves this value in its config gets HTTP **403
`BannedClientAgent`** with the message:
> "This Client-Agent appears badly designed and is causing too many warnings.
> First ensure it provides a proper name and contact details. Then contact us
> on Discord to discuss the issue it's creating."

**This proxy sends:** `ai-horde-oai:0.1:github` — safe, properly formatted.

**Format required:** `<name>:<version>:<contact_url>`

---

## 4. Rate Limits

All limits are enforced by Flask-Limiter with a Redis backend.

### 4.1 IP-based rate limits (applied to generate endpoints)

| Limit | Normal IPs | Whitelisted / Service IPs |
|---|---|---|
| Per minute | 90 req/min | 300 req/min |
| Per second | 2 req/s | 10 req/s |
| Per hour | 90 req/hr | 600 req/hr |

Returns HTTP **429**.

### 4.2 API-key-based rate limit

| Limit | Anonymous key (`0000000000`) | Named key | Whitelisted IP |
|---|---|---|---|
| Per second | 60 req/s | 2 req/s | 60 req/s |

Returns HTTP **429**.

### 4.3 Concurrent request quota

**Source:** `horde/apis/v2/base.py`

Default concurrency limit: **30 simultaneous waiting requests** per user.
Exceeding it returns HTTP **429 `TooManyPrompts`**.
Anonymous users receive a more prominent warning message.

---

## 5. Prompt Content Blocking

### 5.1 Corrupt prompt filter

**Source:** `horde/apis/v2/base.py`, `horde/detection.py`

Prompts are scored against regex/pattern filters. Score ≥ 2 triggers rejection.

Actions taken:
1. Prompt is uploaded to R2 storage for review.
2. User suspicion +1 (see §2.3).
3. IP suspicion reported → Fibonacci timeout applied.
4. Proxy service suspicion incremented (see §5.3).
5. Request rejected: HTTP **400 `CorruptPrompt`**.

Trusted / education accounts: replacement filter applied instead of rejection.
Moderators: never blocked.

### 5.2 NSFW model content filter

Prompts requesting NSFW models combined with prohibited age/youth-related terms are
replaced or rejected. Flagged users are always subject to this filter regardless of
the prompt content.

Returns HTTP **400 `CorruptPrompt`**.

### 5.3 Proxy service suspicion

**Source:** `horde/countermeasures.py` — `report_proxy_suspicion()`

When bad prompts are routed through a proxy service, the proxy's originating IP
accumulates a separate suspicion counter that resets every **1 hour**.
At > 10 events within the hour a Discord alert is sent to Horde staff.

---

## 6. Worker-Level Restrictions

### 6.1 Automatic maintenance (too many aborted jobs)

**Source:** `horde/classes/base/worker.py` — `log_aborted_job()`

| Mode | Threshold (per hour) |
|---|---|
| Normal | 20 aborted jobs |
| Raid mode | 10 aborted jobs |
| Interrogation workers | 100 aborted jobs |

Exceeding the threshold triggers automatic maintenance mode with the message
"Automatic maintenance mode. Please check your worker." The worker is invisible to
new generation requests until the owner manually disables maintenance.

### 6.2 Worker suspicion → auto-pause

**Source:** `horde/classes/base/worker.py` — `report_suspicion()`

Suspicion reasons accumulated per worker:
- `WORKER_NAME_LONG` / `WORKER_NAME_EXTREME`
- `WORKER_PROFANITY` — worker name contains prohibited words
- `UNSAFE_IP` — worker IP flagged as VPN/proxy
- `UNREASONABLY_FAST` — completing jobs faster than plausible
- `TOO_MANY_JOBS_ABORTED` — accumulated abort events

When the suspicion threshold is reached the worker is **automatically paused** and a
Discord notification is sent. Trusted users' workers are never marked suspicious.

### 6.3 Same-IP worker cap

**Source:** `horde/classes/base/user.py` — `exceeding_ipaddr_restrictions()`

| Account type | Max workers per IP |
|---|---|
| Untrusted | 3 |
| Trusted | 20 |
| Invited (admin grant) | N (configured per account) |

Exceeding the cap returns HTTP **403 `TooManySameIPs`** with a Discord contact link.

### 6.4 Worker invite-only mode

When Horde enables invite-only mode, new workers beyond the user's `worker_invited`
quota are rejected with HTTP **403 `WorkerInviteOnly`**.

---

## 7. Raid Mode

When Horde operators activate raid mode all restrictions tighten:

- Untrusted users on VPN/proxy IPs are blocked from generating: HTTP **403 `NotTrusted`**
- Worker aborted-job threshold drops from 20 → 10
- Unsafe IPs are silently rejected without logging warnings

Trusted users, Patrons, and service accounts are unaffected.

---

## 8. Anonymous User Restrictions

Anonymous users (API key `0000000000`) are restricted from:
- Running workers (HTTP **403 `AnonForbiddenWorker`**)
- Using SDXL beta models
- Using styles
- Accessing moderator APIs

All anonymous requests are treated as fully shared (no privacy option).

---

## 9. Account Roles That Remove Restrictions

| Role | Key privileges / exemptions |
|---|---|
| `trusted` | Bypasses IP safety checks; 20 workers/IP; never flagged suspicious; dynamic rate-limit whitelist |
| `service` | Can use `proxied_account` parameter; dynamic rate-limit whitelist |
| `education` | Replacement filter for bad prompts instead of rejection; dynamic rate-limit whitelist |
| `vpn` | IP safety check bypassed for worker registration |
| Patron | IP safety check bypassed; exempt from raid-mode VPN block |

---

## 10. Local Defensive Banning

To avoid "poisoning" the retry loop with known-broken models, this proxy implements local defensive banning:

1. **Unavailable Model Ban**: If a model returns `is_possible=False` (no workers), it is banned locally for **1 hour**.
2. **Continuous Fallback**: On an unavailable model ban, the proxy immediately attempts to re-resolve the requested alias against the remaining filtered model list and retries. This process repeats infinitely (ignoring the `max_retries` config) until a working model is found or the alias has no remaining models left.

---

## 11. What This Means for This Proxy (ai-horde-oai)

| Risk | Assessment |
|---|---|
| Client agent ban | No risk — sends `ai-horde-oai:0.1:github` |
| IP timeout | Low — triggered only by corrupt prompts or repeated abuse |
| VPN/proxy block | Depends on the host machine's IP reputation |
| Rate limit 429 | Possible under heavy traffic; `max_concurrent_requests` config mitigates this |
| Concurrent quota | Mitigated by the `horde_semaphore` in-process limiter |
| Corrupt prompt | Depends entirely on the content sent by the upstream client (OpenClaw) |
| Proxy suspicion | This proxy is treated as a regular client (not a named service account), so the proxy-suspicion path should not apply |

**The highest real-world risk is the corrupt-prompt path**: if OpenClaw routes
user-generated prompts containing prohibited content through this proxy, the Horde
API key and the server's IP will both accumulate suspicion, eventually triggering
Fibonacci-escalating IP timeouts. The key itself can be permanently flagged by a
moderator if the pattern is severe.
