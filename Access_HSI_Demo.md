# Access Domain HSI Demo — PON Resource Allocation Walkthrough

This guide covers the access domain profile, which extends the base TMF921 API with a
real PON resource inventory. When the access profile is active the evaluator resolves
TIO set constructors (`set:resourcesOfType`, `set:resourcesWithPropertyObject`) against
live OLT/ONT/UNI instance data, and the handler writes back `pon:inUse` and
`pon:assignedToService` on the selected UNI and CTAG when an intent is fulfilled.

For the simple single-domain walkthrough without resource inventory, see [`HSI_DEMO.md`](HSI_DEMO.md).

---

## Architecture

```
POST /intent  (expressionValue = hsionlyintent_v0.5.ttl)
       │
       ▼
evaluate_intent()  ←  expression Turtle
                   ←  observation graph  (metric observations)
                   ←  resources graph    (PON inventory — loaded at startup)
       │
       ├─ set:resourcesOfType pon:UNIPort
       │       → enumerates all UNIPort instances from inventory
       ├─ set:resourcesWithPropertyObject (pon:inUse false)
       │       → filters to free UNIs only
       ├─ set:resourcesWithPropertyObject (pon:operationalState pon:Up)
       ├─ set:resourcesWithPropertyObject (pon:provisioningState pon:Ready)
       │
       ├─ set:resourcesOfType pon:CTAGAllocation
       │       → enumerates all CTAG allocations
       ├─ set:resourcesWithPropertyObject (pon:inUse false)
       │       → filters to unallocated CTAGs only
       │
       └─ quantity conditions (DL/UL/latency/jitter/packet-loss)
              → checked against met:Observation records
       │
       ▼
  IntentReport  +  handlerState graph
  write-back: pon:inUse true / pon:assignedToService <intent-uuid>
              on selected UNI and CTAG
```

---

## PON resource topology

The access domain loads `BBF_access/pon_resource_data.ttl` at startup, which defines:

```
OLT-001 (North CO)
  ├─ PON-001-1
  │    ├─ ONT-001  →  UNI-001-1 (free), UNI-001-2 (free)
  │    └─ ONT-002  →  UNI-002-1 (IN USE — pre-assigned), UNI-002-2 (free)
  ├─ PON-001-2
  │    └─ ONT-003  →  UNI-003-1 (free), UNI-003-2 (free)
  └─ UPL-001-1  →  SVLAN-N-HSI (s-tag:100)
                    ├─ CTAG-N-001 (c-tag:200, free)
                    ├─ CTAG-N-002 (c-tag:201, IN USE — BBF_SUB_10001)
                    ├─ CTAG-N-003 (c-tag:202, free)
                    └─ CTAG-N-004 (c-tag:203, free)

OLT-002 (South CO)
  ├─ PON-002-1
  │    ├─ ONT-004  →  UNI-004-1 (free), UNI-004-2 (free)
  │    └─ ONT-005  →  UNI-005-1 (Down — excluded by filter), UNI-005-2 (free)
  └─ UPL-002-1  →  SVLAN-S-HSI (s-tag:100)
                    ├─ CTAG-S-001 (c-tag:200, free)
                    ├─ CTAG-S-002 (c-tag:201, free)
                    └─ CTAG-S-003 (c-tag:202, free)
```

The set constructors in the intent will find **7 free UNIs** (UNI-001-1, UNI-001-2,
UNI-002-2, UNI-003-1, UNI-003-2, UNI-004-1, UNI-004-2, UNI-005-2) and **6 free
CTAGs** (UNI-005-1 is excluded because `pon:operationalState pon:Down`; UNI-002-1 and
CTAG-N-002 are excluded because `pon:inUse true`).

---

## Intent expression structure (`hsionlyintent_v0.5.ttl`)

The HSI intent (`seed_data/hsionlyintent_v0.5.ttl`) defines a top-level `log:allOf`
over five expectations:

| Expectation | Evaluator | What it checks |
|---|---|---|
| `HSIServiceProvisioningExpectation` | `icm:DeliveryExpectation` | Selects a free UNI from `AvailableUNIInterfaces` |
| `UNIOperationalExpectation` | `log:allOf` + `log:match` | Selected UNI has `pon:operationalState pon:Up` AND `pon:provisioningState pon:Ready` |
| `HSICTAGProvisioningExpectation` | `icm:DeliveryExpectation` | Selects a free CTAG from `AvailableCTAGAllocations` |
| `HSIPerformanceExpectation` | `log:allOf` + 5× quantity conditions | DL ≥ 100 Mbps, UL ≥ 20 Mbps, latency < 25 ms, jitter < 3 ms, packet loss < 0.1 % |
| `HSIMonitoringExpectation` | `icm:ReportingExpectation` | Passes silently — not evaluated |

### Set constructor targets

| Target | Filter | Candidates from inventory |
|---|---|---|
| `bbf:AvailableUNIInterfaces` | `pon:UNIPort`, `pon:inUse false`, `pon:operationalState pon:Up`, `pon:provisioningState pon:Ready` | 7 of 10 UNIs |
| `bbf:AvailableCTAGAllocations` | `pon:CTAGAllocation`, `pon:inUse false` | 6 of 7 CTAGs |

---

## Prerequisites

### Option A — Docker (recommended)

```bash
sudo docker compose down -v   # clean slate — remove stale volumes
sudo docker compose --profile access up --build
```

This starts Fuseki (port 3030) and the access API (port 8001). The API loads the PON
resource inventory from `BBF_access/` at startup. Wait for:

```
INFO:     Application startup complete.
```

Verify the inventory was loaded by checking the startup logs for:

```
INFO  Loaded resource data pon_resource_data.ttl into resources graph
INFO  Resource inventory loaded from BBF_access (1 files)
```

### Option B — Local dev server

```bash
# Terminal 1 — Fuseki (must be running before the API starts)
sudo docker run -p 3030:3030 \
  -v "$(pwd)/fuseki-config.ttl:/fuseki/config.ttl" \
  --name fuseki stain/jena-fuseki:5.2.0 --config /fuseki/config.ttl

# Terminal 2 — Access domain API
FUSEKI_DATASET=tmf921-access RESOURCE_DATA_DIR=BBF_access \
    .venv/bin/uvicorn src.main:app --port 8001 --reload
```

### Verify health

```bash
curl http://localhost:8001/health
```

Expected:
```json
{"status": "UP", "graph": "UP"}
```

---

## Step 1 — Seed the HSI intent

The access seed script posts the full `hsionlyintent_v0.5.ttl` expression to the
access domain:

```bash
python seed_data/seed_access.py --base-url http://localhost:8001
```

The script outputs the created intent's ID and the next steps. Capture the intent ID:

```bash
ACCESS_INTENT=$(curl -s "http://localhost:8001/tmf-api/intentManagement/v5/intent" \
  | python3 -c "import sys,json; intents=json.load(sys.stdin); print(intents[0]['id']) if intents else print('none')")
echo "Access intent ID: $ACCESS_INTENT"
```

---

## Step 2 — Check the initial report

Immediately after creation the evaluator fires. The structural conditions
(UNI selection, CTAG selection, UNI operational state) resolve against the loaded
inventory. The five performance conditions degrade until observations are posted.

```bash
sleep 1

curl -s "http://localhost:8001/tmf-api/intentManagement/v5/intent/$ACCESS_INTENT/intentReport" \
  | python3 -m json.tool
```

Expected `intentHandlingState`: **`Degraded`** — performance metric observations are missing.

The structural conditions (`UNIOperationalExpectation`, `HSICTAGProvisioningExpectation`)
pass because the inventory contains matching free resources. Only the five quantity
conditions fail.

---

## Step 3 — Submit metric observations

Post observations to the access domain (port 8001):

```bash
BASE="http://localhost:8001/tmf-api/intentManagement/v5/intent/$ACCESS_INTENT/observation"
BBF="http://broadband-forum.org/Intent#"

# Downstream: 150 Mbps (threshold >= 100)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}DownstreamBandwidthMetric\", \"value\": 150.0}"

# Upstream: 30 Mbps (threshold >= 20)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}UpstreamBandwidthMetric\", \"value\": 30.0}"

# Latency: 8 ms (threshold < 25)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}LatencyMetric\", \"value\": 8.0}"

# Jitter: 1.2 ms (threshold < 3)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}JitterMetric\", \"value\": 1.2}"

# Packet loss: 0.02 % (threshold < 0.1)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}PacketLossMetric\", \"value\": 0.02}"
```

---

## Step 4 — Verify Fulfilled state

```bash
sleep 2

curl -s "http://localhost:8001/tmf-api/intentManagement/v5/intent/$ACCESS_INTENT/intentReport" \
  | python3 -m json.tool
```

Expected `intentHandlingState`: **`Fulfilled`**

---

## Step 5 — Inspect UNI and CTAG resource state

Query the resources named graph in the access domain's Fuseki dataset to confirm
write-back has occurred:

```bash
curl -s -X POST "http://localhost:3030/tmf921-access/sparql" \
  -H "Content-Type: application/sparql-query" \
  -d "
PREFIX pon: <http://broadband-forum.org/ont/pon-resource#>

SELECT ?uni ?inUse ?assignedTo
WHERE {
  GRAPH <http://tmforum.org/api/v5/resources> {
    ?uni a pon:UNIPort ;
         pon:inUse ?inUse .
    OPTIONAL { ?uni pon:assignedToService ?assignedTo }
  }
}
ORDER BY ?uni
" | python3 -m json.tool
```

Expected: the selected UNI shows `pon:inUse true` and `pon:assignedToService` set to
`$ACCESS_INTENT`. All other free UNIs remain `false`.

Query the selected CTAG allocation:

```bash
curl -s -X POST "http://localhost:3030/tmf921-access/sparql" \
  -H "Content-Type: application/sparql-query" \
  -d "
PREFIX pon: <http://broadband-forum.org/ont/pon-resource#>

SELECT ?ctag ?cTag ?inUse ?assignedTo
WHERE {
  GRAPH <http://tmforum.org/api/v5/resources> {
    ?ctag a pon:CTAGAllocation ;
          pon:cTag  ?cTag ;
          pon:inUse ?inUse .
    OPTIONAL { ?ctag pon:assignedToService ?assignedTo }
  }
}
ORDER BY ?cTag
" | python3 -m json.tool
```

Expected: exactly one CTAG shows `pon:inUse true` with `pon:assignedToService` matching
the intent UUID. The pre-existing CTAG-N-002 (`c-tag:201`) remains `true` with its
existing `pon:subscriberId "BBF_SUB_10001"` unchanged.

---

## Step 6 — Verify via the handlerState graph

Check which specific UNI and CTAG were recorded in the OODA working memory:

```bash
curl -s -X POST "http://localhost:3030/tmf921-access/sparql" \
  -H "Content-Type: application/sparql-query" \
  -d "
PREFIX imo: <http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/>

SELECT ?type ?observed ?bound ?selected ?passed
WHERE {
  GRAPH <http://tmforum.org/api/v5/intents/$ACCESS_INTENT/handlerState> {
    ?intent imo:hasConditionResult ?c .
    ?c a ?type ;
       imo:conditionPassed ?passed .
    OPTIONAL { ?c imo:observedValue   ?observed }
    OPTIONAL { ?c imo:boundValue      ?bound }
    OPTIONAL { ?c imo:selectedResource ?selected }
  }
}
ORDER BY ?type
" | python3 -m json.tool
```

Expected: the two `DeliveryExpectation` rows each show a `selected` URI — one for the
chosen `pon:UNIPort` and one for the chosen `pon:CTAGAllocation`. The quantity conditions
(`atLeast`, `smaller`) show numeric `observed`/`bound` pairs.

---

## Step 7 — Degrade and recover

### Violate a performance condition

```bash
# Latency: 40 ms (threshold < 25 — VIOLATED)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}LatencyMetric\", \"value\": 40.0}"

sleep 1

curl -s "http://localhost:8001/tmf-api/intentManagement/v5/intent/$ACCESS_INTENT/intentReport" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r[0]['intentHandlingState'], '-', r[0].get('intentHandlingReason',''))"
```

Expected: **`Degraded`** — `40 < 25: FAIL`

The UNI and CTAG remain reserved — the intent is degraded, not terminated. A new
within-threshold observation recovers the intent without releasing the resources.

### Recover

```bash
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}LatencyMetric\", \"value\": 12.0}"

sleep 1

curl -s "http://localhost:8001/tmf-api/intentManagement/v5/intent/$ACCESS_INTENT/intentReport" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r[0]['intentHandlingState'])"
```

Expected: **`Fulfilled`**

---

## Step 8 — F-interface demo (aggregation → access domain)

This step requires **both** profiles running simultaneously.

### Start both domains

```bash
# Terminal 1 — start both domains together
sudo docker compose --profile access --profile aggregation up --build
```

Wait for both `access-api` (port 8001) and `agg-api` (port 8000) to log
`Application startup complete`. The access domain loads the PON inventory automatically.

### Architecture

```
Aggregation domain (:8000)          Access domain (:8001)
tmf921-agg dataset                  tmf921-access dataset
                                    PON inventory loaded at startup

   seed_aggregation.py
        │
        ├─ 1. Register hub subscription on access domain
        │      POST :8001/hub  callback → :8000/listener
        │
        ├─ 2. POST ProbeIntent → :8001/intent
        │      expression: "can you deliver ≥ 100 Mbps with an available UNI?"
        │
        │      Access domain evaluates:
        │        - DeliveryExpectation + set:resourcesOfType pon:UNIPort
        │        - set:resourcesWithPropertyObject (inUse false, opState Up)
        │        - passes if ≥1 free UNI found in inventory (no observation needed)
        │        - auto-transitions probe: ACTIVE (pass) or TERMINATED (fail)
        │
        ├─ 3. Poll :8001/intent/{probeId} for ACTIVE or TERMINATED
        │
        └─ 4. If ACTIVE → POST full HSI Intent → :8001/intent
```

### Run the automated demo

```bash
# Terminal 2
python seed_data/seed_aggregation.py \
    --agg-url  http://localhost:8000 \
    --access-url http://localhost:8001
```

A successful run prints each step and ends with:

```
Probe result: lifecycleStatus = ACTIVE
Probe passed — posting full HSI Intent to access domain…
  Created Intent: <uuid> — AGG→ACCESS: HSI service request — BBF_SUB_12345
  Monitor: GET http://localhost:8001/tmf-api/intentManagement/v5/intent/<uuid>
```

### Post performance observations for the HSI intent

The HSI intent has five performance conditions (bandwidth, jitter, latency, packet loss,
availability). It starts Degraded until observations are posted. Copy the intent UUID
from the seed script output and set it:

```bash
F_INTENT=<uuid-from-seed-aggregation-output>
BBF="http://broadband-forum.org/Intent#"
BASE="http://localhost:8001/tmf-api/intentManagement/v5/intent/$F_INTENT/observation"

# Downstream bandwidth: 150 Mbps (≥ 100)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}DownstreamBandwidthMetric\", \"value\": 150.0}"

# Upstream bandwidth: 30 Mbps (≥ 20)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}UpstreamBandwidthMetric\", \"value\": 30.0}"

# Latency: 8 ms (< 25)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}LatencyMetric\", \"value\": 8.0}"

# Packet loss: 0.02% (< 0.1)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}PacketLossMetric\", \"value\": 0.02}"

# Jitter: 1.2 ms (< 3.0)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}JitterMetric\", \"value\": 1.2}"

sleep 2

# Confirm Fulfilled
curl -s "http://localhost:8001/tmf-api/intentManagement/v5/intent/$F_INTENT/intentReport" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r[0]['intentHandlingState'])"
```

Expected: **`Fulfilled`** — all DeliveryExpectation and performance conditions pass.

### Verify resource state after the F-interface demo

```bash
curl -s -X POST "http://localhost:3030/tmf921-access/sparql" \
  -H "Content-Type: application/sparql-query" \
  -d "
PREFIX pon: <http://broadband-forum.org/ont/pon-resource#>

SELECT ?uni ?inUse ?assignedTo
WHERE {
  GRAPH <http://tmforum.org/api/v5/resources> {
    ?uni a pon:UNIPort ;
         pon:inUse ?inUse .
    OPTIONAL { ?uni pon:assignedToService ?assignedTo }
  }
}
ORDER BY ?uni
" | python3 -m json.tool
```

Expected: one UNI shows `pon:inUse true` and `pon:assignedToService` set to `$F_INTENT`
(the HSI intent UUID posted by the aggregation domain). All other free UNIs remain `false`.

### If the probe fails

The probe resolves purely against the resource inventory — no metric observations
are needed. It fails only if all UNIs in the inventory are already in use
(`pon:inUse true`) or operationally down. Verify the resource state:

```bash
curl -s -X POST "http://localhost:3030/tmf921-access/sparql" \
  -H "Content-Type: application/sparql-query" \
  -d "
PREFIX pon: <http://broadband-forum.org/ont/pon-resource#>
SELECT ?uni ?inUse ?opState WHERE {
  GRAPH <http://tmforum.org/api/v5/resources> {
    ?uni a pon:UNIPort ; pon:inUse ?inUse ; pon:operationalState ?opState .
  }
}" | python3 -m json.tool
```

If all UNIs are in use, restart with `sudo docker compose down -v` to reset the
resource inventory to its initial state (7 free UNIs).

---

## Condition reference

| Condition | Type | Threshold | Pass example | Fail example |
|---|---|---|---|---|
| Downstream BW | `quan:atLeast` | ≥ 100 Mbps | 150 | 80 |
| Upstream BW | `quan:atLeast` | ≥ 20 Mbps | 30 | 15 |
| Latency | `quan:smaller` | < 25 ms | 8 | 40 |
| Jitter | `quan:smaller` | < 3 ms | 1.2 | 5 |
| Packet loss | `quan:smaller` | < 0.1 % | 0.02 | 0.2 |
| UNI operational | `log:match` | `pon:operationalState pon:Up` | UNI in inventory | `pon:Down` UNI excluded by set filter |
| UNI ready | `log:match` | `pon:provisioningState pon:Ready` | UNI in inventory | `pon:Configuring` UNI excluded |
| UNI available | set constructor | `pon:inUse false` | free UNI | UNI-002-1 excluded |
| CTAG available | set constructor | `pon:CTAGAllocation`, `pon:inUse false` | free CTAG | CTAG-N-002 excluded |

---

## What to look at in the code

| Location | What it does |
|---|---|
| `BBF_access/pon_resource_onto.ttl` | OWL ontology for OLT/ONT/UNI/PON port/SVLAN/CTAG resources |
| `BBF_access/pon_resource_data.ttl` | Instance data — 2 OLTs, 5 ONTs, 10 UNI ports, SVLAN/CTAG pools |
| `seed_data/hsionlyintent_v0.5.ttl` | Full HSI intent expression with set constructors and performance conditions |
| `seed_data/seed_access.py` | Seeds the HSI intent to the access domain |
| `seed_data/seed_aggregation.py` | F-interface demo: hub → ProbeIntent → poll → full HSI intent |
| `src/graph/schema_init.py:load_resources()` | Loads `RESOURCE_DATA_DIR` TTL files into the resources named graph at startup |
| `src/handler/evaluator.py:evaluate_intent()` | Merges resources graph into evaluation context so set constructors resolve against inventory |
| `src/handler/evaluator.py:_compute_set_constructors()` | Executes `set:resourcesOfType` and `set:resourcesWithPropertyObject` against the merged graph |
| `src/handler/dispatcher.py:dispatch_evaluation()` | Orchestrates evaluate → write state → create report → notify → write-back |
| `src/graph/namespaces.py:RESOURCES_GRAPH` | Named graph URI for the loaded resource inventory |
| `docker-compose.yml` `access-api` service | Profile definition, port 8001, `FUSEKI_DATASET`, `RESOURCE_DATA_DIR` |
