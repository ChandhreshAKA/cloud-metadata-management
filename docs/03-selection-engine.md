# Chunk 3: Provider registry and selection engine

Status: built and tested. **275 unit tests pass; 29 integration tests pass against Oracle 26ai Free**
(run at build time with the project's `.env`). Every price, score and latency in this chapter is
**ILLUSTRATIVE**: invented simulation values, not vendor data and not measurements.

## 1. What was built

| Module | Where | Role |
|---|---|---|
| 3. Provider registry | `app/providers/`, `app/db/repositories/providers.py`, `app/api/routers/providers.py` | register, configure, enable/disable, availability, pricing; reads for all roles, writes ADMIN only |
| 4. Selection engine | `app/selection/engine.py` (+ `models.py`, `presets.py`, `explain.py`, `baselines.py`) | a **pure function**: data in, result out |
| Glue and API | `app/selection/service.py`, `app/api/routers/selection.py` | load data in one read-only transaction, call the engine, serialise |
| Worked example | `app/selection/example.py`, `scripts/worked_example.py` | the numbers in section 5, printed by the code |

The engine reads no database, no file, no clock and no random source. `select(request, candidates,
weights, policy)` returns the same result for the same input and does not modify its inputs. Upload
(chunk 4) calls it unchanged; replica placement (chunk 5) can call it again with a different candidate
list; the savings estimate (chunk 6) reuses `estimate_cost`.

A candidate is a **provider-region** (chunk 1 design decision D-1), priced for the requested tier.

## 2. The algorithm

### Step 1: hard constraints, before any ranking
A provider-region is rejected if **any** of these fail. Every failing reason is recorded and shown.

| Constraint | Source |
|---|---|
| provider enabled | `cloud_providers.is_active` |
| region enabled | `provider_regions.is_active` |
| availability: not `DOWN` (`DEGRADED` is allowed, with a warning) | `provider_regions.health_status` |
| priced for the requested tier | `provider_pricing` |
| has a latency measurement | median of the latest 20 successful GET probes (`performance_measurements`) |
| **size fit**: file size <= provider limit | `cloud_providers.max_object_bytes` |
| security score >= minimum for the file's level | `system_config` key `security.min_score.<LEVEL>` |
| reliability score >= minimum for the file's level | `system_config` key `selection.min_reliability.<LEVEL>`; defaults LOW 0, MEDIUM 9.0, HIGH 9.4 |

Size fit is a filter, never a scored criterion. Boundaries are inclusive: a score exactly equal to the
minimum passes, and so does a file exactly at the size limit.

### Step 2: estimated cost for THIS file
```
size_gb        = file_size_bytes / 2^30                      (GB means GiB, as everywhere in this project)
billed_months  = max(retention_months, min_storage_days / 30)
reads          = expected_accesses_per_month * retention_months
storage_cost   = size_gb * storage_usd_per_gb_month * billed_months
request_cost   = put_usd_per_1k / 1000 * 1  +  get_usd_per_1k / 1000 * reads
retrieval_cost = size_gb * retrieval_usd_per_gb * reads
egress_cost    = size_gb * egress_usd_per_gb * reads
transfer_cost  = request_cost + retrieval_cost + egress_cost
total_cost     = storage_cost + transfer_cost
```
Money is rounded to 8 decimals (half-even). `billed_months` charges the tier's minimum storage period even
if the file is kept for less, so a cold tier is not "cheap" for a one-month file. Assumptions: one upload
(one PUT); each read downloads the whole file to the internet; the egress price belongs to the region, not
to the tier.

### Step 3: normalise (min-max) across the eligible candidates only
Five metrics: **storage cost, transfer and request cost, latency** (lower is better) and
**reliability, security** (higher is better).
```
n = (x - min) / (max - min)        rounded to 6 decimals, half-even
```
**All values equal (max = min), or only one candidate:** there is nothing to compare, so every candidate
scores **1.0** on that metric and nothing is divided by zero. (`n` is reported as 0 for minimised and 1 for
maximised metrics, so the formulas in step 4 still hold.) Rejected candidates never take part, so a DOWN
provider cannot distort the others' scores.

### Step 4: direction
`score = 1 - n` for cost and latency; `score = n` for reliability and security. **A higher score is always
better, and so is a higher composite.**

### Step 5: weighted sum
`composite = sum(weight_i * score_i)`, rounded to 6 decimals. Weights come from the `weight_profiles` table
(the database enforces that they sum to exactly 1):

| Preset | storage | transfer | latency | reliability | security |
|---|---|---|---|---|---|
| `BALANCED` (default) | 0.25 | 0.15 | 0.20 | 0.25 | 0.15 |
| `COST_SAVER` (cost-focused) | 0.40 | 0.20 | 0.10 | 0.20 | 0.10 |
| `PERFORMANCE` (performance-focused) | 0.10 | 0.10 | 0.50 | 0.20 | 0.10 |
| `SECURE` (security-focused) | 0.10 | 0.05 | 0.10 | 0.25 | 0.50 |

**Per-request overrides** replace any of the five weights. Negative, non-numeric, non-finite or unknown
weights are rejected (HTTP 422), as is an all-zero set. If the final weights do not sum to 1 they are
**divided by their sum and the response says so** (`weights_note`, repeated in `warnings`), for example
"weights summed to 1.3, so each was divided by 1.3 to sum to 1".

### Step 6: deterministic tie-breaking
Ranking uses the **rounded** composite (the number you see). Ties are broken in this order and the result
is then final:

1. higher security score
2. higher reliability score
3. lower total cost
4. lower `provider_id`
5. lower `region_id`

### Output
A ranked list; for each candidate its raw metric values, normalised values, scores, exact weighted
contributions, composite and cost breakdown; the selected provider; advantages and disadvantages of the
winner relative to the runner-up; every rejection with all of its reasons; a plain-English explanation
(including a sentence when the winner is **not** the cheapest eligible option); warnings (illustrative data,
normalised weights, degraded provider); the cost formulas; and the tie-break order.

## 3. Baselines (for the evaluation in chunk 8)

| Baseline | What it does | Notes |
|---|---|---|
| `cheapest_only` | the eligible candidate with the lowest `total_cost` | ties: lower provider id, then region id. Ignores latency, reliability and security beyond the hard minimums |
| `fixed_provider(code)` | always the named provider (its lowest-numbered eligible region) | reports why it cannot be used if it fails a constraint |

Both obey the **same hard constraints** as the engine, so a baseline cannot "win" by breaking a rule.

## 4. Provider registry (module 3) and endpoints

Capabilities available in the chunk 1 schema: maximum object size, the storage tiers a region is priced
for, availability (`health_status`), and the reliability and security scores. There are no separate
capability flags.

| Method and path | Who | Purpose |
|---|---|---|
| `GET /providers` | any role (`provider:read`) | providers with regions, scores, availability and per-tier pricing |
| `POST /providers` | ADMIN (`provider:manage`) | register a provider (code `A-Z0-9_`, 2 to 20 characters) |
| `PATCH /providers/{id}` | ADMIN | rename, change the size limit, enable or disable (`is_active`) |
| `POST /providers/{id}/regions` | ADMIN | register a region (scores 0 to 10, egress price, bucket) |
| `PATCH /regions/{id}` | ADMIN | scores, egress, bucket, availability (`health_status`), enable or disable |
| `PUT /regions/{id}/pricing/{tier}` | ADMIN | create or replace a tier's prices |
| `POST /selection/compare` | any role | every region side by side, the winner under each preset, and the cheapest baseline (**dry run**) |
| `POST /selection/select` | any role | rank, explain and report baselines (**dry run**) |

Every change and its audit event commit in one transaction (`CONFIG_CHANGED`, or `PROVIDER_STATUS_CHANGED`
for enable/disable and availability, with old and new values). A non-admin write returns 403 and writes an
`ACCESS_DENIED` audit row. A newly registered region is **ineligible until it has pricing for the tier and
at least one latency probe**, and the response says exactly that.

Dry runs write nothing (verified against Oracle: the row counts of `audit_events` and `selection_decisions`
do not change). Upload (chunk 4) will store the decision in `selection_decisions` and
`selection_candidates`.

PowerShell example (after logging in and setting `$auth`, see the README):
```powershell
$body = @{ file_size_bytes = 1073741824; security_level = "HIGH"; tier = "HOT"; preset = "SECURE" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/selection/select -Headers $auth -ContentType "application/json" -Body $body
```

## 5. Worked example (AWS, Azure, Google Cloud): ILLUSTRATIVE numbers

The file is 10 GiB, `MEDIUM` security, tier `HOT`, kept 12 months, read 4 times a month. The prices,
scores and latencies were made up so that the arithmetic is easy to follow and so that **the cheapest
provider is not the best overall**. The output below is printed by `scripts/worked_example.py`. The test
`tests/unit/test_selection_worked_example.py` asserts the same numbers, which were calculated by hand (and
cross-checked with exact fractions, independent of the engine) before the test was written.

Hand-check of the key numbers:
* AWS storage = 10 x 0.023 x 12 = 2.76. Egress = 10 x 0.09 x 48 reads = 43.2.
  Requests = 0.005/1000 + 0.0004/1000 x 48 = 0.0000242. Transfer = 0.0000242 + 0 + 43.2 = 43.2000242.
* AWS storage normalised = (2.76 - 2.40) / (3.12 - 2.40) = 0.36 / 0.72 = 0.5.
* AWS transfer normalised = (43.2000242 - 41.76002662) / (57.6000242 - 41.76002662)
  = 1.43999758 / 15.83999758 = 0.090909 (6 decimals), so its score is 1 - 0.090909 = 0.909091.
* AWS BALANCED composite = 0.25 x 0.5 + 0.15 x 0.909091 + 0.20 x 1 + 0.25 x 1 + 0.15 x 0.5
  = 0.125 + 0.13636365 + 0.2 + 0.25 + 0.075 = 0.78636365, shown as 0.786364.

```text
WORKED EXAMPLE - every price, score and latency below is ILLUSTRATIVE (made up)

File: 10737418240 bytes (10 GiB), MEDIUM security, tier HOT, kept 12 months, 4 reads a month

Cost formulas:
  size_gb = file_size_bytes / 2^30
  billed_months = max(retention_months, min_storage_days / 30)
  reads = expected_accesses_per_month * retention_months
  storage_cost = size_gb * storage_usd_per_gb_month * billed_months
  request_cost = put_usd_per_1k / 1000 * 1 + get_usd_per_1k / 1000 * reads
  retrieval_cost = size_gb * retrieval_usd_per_gb * reads
  egress_cost = size_gb * egress_usd_per_gb * reads
  transfer_cost = request_cost + retrieval_cost + egress_cost
  total_cost = storage_cost + transfer_cost
  money is rounded to 8 decimals; normalised values and composites to 6 (half-even)

STEP 1  Inputs per provider
provider      storage $/GB-mo  put $/1k  get $/1k  egress $/GB  latency ms  reliab.  security
------------  ---------------  --------  --------  -----------  ----------  -------  --------
AWS           0.023            0.005     0.0004    0.09         40          9.5      9.2     
Azure         0.02             0.0055    0.00044   0.087        60          9.4      9.3     
Google Cloud  0.026            0.005     0.0004    0.12         50          9.3      9.1     

STEP 2  Hard constraints (all pass: nothing is DOWN or disabled, size fits, MEDIUM minimums met)
        eligible: 3  rejected: 0

STEP 3  Cost for this file (reads = 4 x 12 = 48, size_gb = 10)
provider      storage  request     retrieval  egress  transfer     total      
------------  -------  ----------  ---------  ------  -----------  -----------
AWS           2.76     0.0000242   0          43.2    43.2000242   45.9600242 
Azure         2.4      0.00002662  0          41.76   41.76002662  44.16002662
Google Cloud  3.12     0.0000242   0          57.6    57.6000242   60.7200242 

STEP 4  Min-max normalisation n = (x - min) / (max - min), 6 decimals; then score
        (cost and latency: score = 1 - n; reliability and security: score = n)
  storage cost
provider      raw   n    score
------------  ----  ---  -----
AWS           2.76  0.5  0.5  
Azure         2.4   0    1    
Google Cloud  3.12  1    0    

  transfer and request cost
provider      raw          n         score   
------------  -----------  --------  --------
AWS           43.2000242   0.090909  0.909091
Azure         41.76002662  0         1       
Google Cloud  57.6000242   1         0       

  latency
provider      raw  n    score
------------  ---  ---  -----
AWS           40   0    1    
Azure         60   1    0    
Google Cloud  50   0.5  0.5  

  reliability
provider      raw  n    score
------------  ---  ---  -----
AWS           9.5  1    1    
Azure         9.4  0.5  0.5  
Google Cloud  9.3  0    0    

  security
provider      raw  n    score
------------  ---  ---  -----
AWS           9.2  0.5  0.5  
Azure         9.3  1    1    
Google Cloud  9.1  0    0    

STEP 5  Weighted sum, preset BALANCED: storage_cost 0.25, transfer_cost 0.15, latency 0.2, reliability 0.25, security 0.15
provider      storage_cost  transfer_cost  latency  reliability  security  exact sum   composite  rank
------------  ------------  -------------  -------  -----------  --------  ----------  ---------  ----
AWS           0.125         0.13636365     0.2      0.25         0.075     0.78636365  0.786364   1   
Azure         0.25          0.15           0        0.125        0.15      0.675       0.675      2   
Google Cloud  0             0              0.1      0            0         0.1         0.1        3   

        winner under BALANCED: AWS (ap-south-1)

STEP 5  Weighted sum, preset COST_SAVER: storage_cost 0.4, transfer_cost 0.2, latency 0.1, reliability 0.2, security 0.1
provider      storage_cost  transfer_cost  latency  reliability  security  exact sum  composite  rank
------------  ------------  -------------  -------  -----------  --------  ---------  ---------  ----
AWS           0.2           0.1818182      0.1      0.2          0.05      0.7318182  0.731818   2   
Azure         0.4           0.2            0        0.1          0.1       0.8        0.8        1   
Google Cloud  0             0              0.05     0            0         0.05       0.05       3   

        winner under COST_SAVER: Azure (Central India)

STEP 5  Weighted sum, preset PERFORMANCE: storage_cost 0.1, transfer_cost 0.1, latency 0.5, reliability 0.2, security 0.1
provider      storage_cost  transfer_cost  latency  reliability  security  exact sum  composite  rank
------------  ------------  -------------  -------  -----------  --------  ---------  ---------  ----
AWS           0.05          0.0909091      0.5      0.2          0.05      0.8909091  0.890909   1   
Azure         0.1           0.1            0        0.1          0.1       0.4        0.4        2   
Google Cloud  0             0              0.25     0            0         0.25       0.25       3   

        winner under PERFORMANCE: AWS (ap-south-1)

STEP 5  Weighted sum, preset SECURE: storage_cost 0.1, transfer_cost 0.05, latency 0.1, reliability 0.25, security 0.5
provider      storage_cost  transfer_cost  latency  reliability  security  exact sum   composite  rank
------------  ------------  -------------  -------  -----------  --------  ----------  ---------  ----
AWS           0.05          0.04545455     0.1      0.25         0.25      0.69545455  0.695455   2   
Azure         0.1           0.05           0        0.125        0.5       0.775       0.775      1   
Google Cloud  0             0              0.05     0            0         0.05        0.05       3   

        winner under SECURE: Azure (Central India)

STEP 6  Tie-break order: higher composite (rounded to 6 decimals); higher security score; higher reliability score; lower total cost; lower provider_id; lower region_id 

RESULT (BALANCED)
Selected AWS (ap-south-1) with a composite score of 0.786364 using the BALANCED weights (storage cost 0.25, transfer and request cost 0.15, latency 0.2, reliability 0.25, security 0.15). Runner-up: Azure (Central India) with 0.675 (0.111364 behind). The lead comes from latency (+0.2), reliability (+0.125). Azure (Central India) is better on storage cost (0.125 of score), security (0.075 of score), transfer and request cost (0.01363635 of score), which was not enough to overcome the lead. It is NOT the cheapest eligible option: Azure (Central India) costs $44.16002662 over 12 months versus $45.9600242 ($1.79999758 more for the winner) but ranked #2 because the other metrics, weighted as above, outweighed price. 

Advantages of the winner over the runner-up:
  + latency: 40 ms vs 60 ms (20 ms lower)
  + reliability: 9.5 vs 9.4 (0.1 higher)
Disadvantages:
  - storage cost: $2.76 vs $2.4 ($0.36 higher)
  - transfer and request cost: $43.2000242 vs $41.76002662 ($1.43999758 higher)
  - security: 9.2 vs 9.3 (0.1 lower)

BASELINES
  cheapest_only : Azure (Central India), total $44.16002662
  fixed_provider: Google Cloud (asia-south1), total $60.7200242
```

What this example shows:
* **The cheapest provider is correctly not chosen.** Azure has the lowest total cost ($44.16002662), but
  under `BALANCED` weights AWS wins (0.786364 vs 0.675) because it is faster and more reliable.
* **The weights change the winner.** `BALANCED` and `PERFORMANCE` pick AWS; `COST_SAVER` and `SECURE` pick Azure.
* **Google Cloud never wins here**: it is the most expensive, has middle latency, and is last on reliability
  and security.

### A second scenario set (three providers with a clear specialty each)
`tests/unit/test_selection_scenarios.py`: CheapCo (lowest price, 200 ms), FastCo (10 ms, dearest), SecureCo
(best security and reliability). Same 1 GiB file, `MEDIUM`, 12 months, 1 read a month.

| Preset | Winner | Composites (winner first) |
|---|---|---|
| `COST_SAVER` | CheapCo | 0.600000; SecureCo 0.482105; FastCo 0.250000 |
| `PERFORMANCE` | FastCo | 0.650000; SecureCo 0.555526; CheapCo 0.200000 |
| `SECURE` | SecureCo | 0.827105; FastCo 0.287500; CheapCo 0.150000 |
| `BALANCED` | SecureCo | 0.576711; CheapCo 0.400000; FastCo 0.387500 |

Three different presets give three different winners, and under `BALANCED` the cheapest (CheapCo) is not
chosen. For a `HIGH`-security file CheapCo is **excluded outright** (reliability 9.2 is below the 9.4
minimum) and `cheapest_only` skips it too.

## 6. How this was verified

| Check | Result |
|---|---|
| Unit tests (fake database) | 275 pass; engine, serialisation and routers at 100% line coverage |
| Worked example | the engine output equals the hand calculation exactly (costs, normalised values, scores, composites, ranks) |
| Oracle integration tests (rerun them yourself) | 29 pass: candidates load as exact `Decimal`, weight profiles equal the code presets, the API equals the engine run on the same Oracle data, registry writes and audit rows, MERGE upsert, constraint errors, dry runs write nothing |
| Deliberate breakage ("mutation") of the engine | 8 changes (zero-range guard, score direction, boundary, tie-break order twice, a cost term, size filter, weight normalisation) each made at least one test fail. One survived the first time (tie-break order), which exposed a weak test; it was strengthened and now fails as it should |
| Real Oracle found two issues the fake could not | the schema's one-class-name-per-region rule (`uq_pricing_native`: now a clear 409 message and modelled in the fake) and the test cleanup order (probe rows reference regions) |

## 7. Limitations and honest notes

* **All prices, scores, latencies and size limits are illustrative** (`is_illustrative = 'Y'`). The output
  says so in `warnings`. Real use needs real price lists and measured latency.
* **Latency is simulated.** The seeded probes are made-up numbers (`source = 'SIMULATED'`), assuming a client
  in India. Nothing here measures a real network.
* **Cost inputs are caller estimates.** `expected_accesses_per_month` and `retention_months` are supplied by
  whoever uploads; wrong guesses give wrong costs. Each read is assumed to download the whole file.
* **Min-max normalisation is relative.** Scores depend on which candidates are eligible: adding or removing a
  provider can change the normalised values of the others and, in rare cases, reverse the order of two
  providers that were both already present (rank reversal). This is a known property of the method, not a
  bug. Weights express relative importance, not absolute thresholds.
* **One tier per request.** The engine prices the single requested tier (default `HOT`); it does not choose a
  tier. Archive recommendations belong to chunk 6.
* **Scores are admin-entered** (0 to 10) and the engine trusts them. `DEGRADED` regions stay eligible with a
  warning; only `DOWN` is excluded.
* **Not implemented here:** persisting the decision (chunk 4), replica placement (chunk 5), tier migration
  savings (chunk 6), per-user policy, currency conversion, taxes, volume discounts.
