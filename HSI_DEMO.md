# HSI Intent Demo — End-to-End Walkthrough

This guide shows how to exercise the BBF High-Speed Internet (HSI) service intent
through the TMF921 API, verify that the intent handler evaluates conditions correctly,
and observe how the `intentHandlingState` transitions between `Fulfilled` and `Degraded`
as metric observations are submitted.

---

## Architecture recap

```
POST /observation  →  observation graph (Fuseki named graph)
                           ↓ merged at evaluation time
POST /intent       →  expression Turtle (Fuseki named graph)
                           ↓
               evaluate_turtle_conditions()   ← pure Python / RDFLib
                           ↓
               IntentReport  +  handlerState graph
```

The evaluator runs as a background task every time an intent is created, patched,
or receives a new observation. It merges the expression Turtle with the observation
graph, then walks the TIO expression tree to produce `Fulfilled` or `Degraded`.

---

## Start with a clean Fuseki store

Fuseki uses TDB2 persistent storage. If you have previously run the seeder or an
older version of the API, stale IntentReports from before Phase 5 may already be in
the store. Always wipe and restart before running this demo to avoid seeing old data.

### Docker (recommended — wipes volumes on restart)

```bash
sudo docker compose down -v   # -v removes named volumes → clean Fuseki store
sudo docker compose up --build
```

### Dev server (wipe Fuseki TDB2 manually)

Stop both the API and Fuseki, then delete the Fuseki data directory before
restarting:

```bash
sudo rm -rf /tmp/fuseki-data   # adjust to your fuseki --loc path
# then restart Fuseki and the API as normal
```

---

## Prerequisites

### Option A — Docker (recommended)

```bash
sudo docker compose up --build
```

This starts Fuseki (port 3030) and the API (port 8000) together. Wait for:
```
INFO:     Application startup complete.
```

### Option B — Local dev server

```bash
# Terminal 1 — Fuseki
sudo docker run -p 3030:3030 \
  -v "$(pwd)/fuseki-config.ttl:/fuseki/config.ttl" \
  --name fuseki stain/jena-fuseki:5.2.0 --config /fuseki/config.ttl

# Terminal 2 — API
.venv/bin/uvicorn src.main:app --reload
```

### Verify health

```bash
curl http://localhost:8000/health
```

Expected:
```json
{"status": "UP", "graph": "UP"}
```

---

## The HSI intent structure

The intent expression (`seed_data/hsionlyintent_v0.5.ttl`) defines a top-level
`log:allOf` over four expectations:

| Expectation | Evaluator | What it checks |
|---|---|---|
| `HSIServiceProvisioningExpectation` | `icm:DeliveryExpectation` | UNI target container has a member typed `bbf:HSIService` |
| `UNIOperationalExpectation` | `log:allOf` + `log:match` | `SelectedUNIInterface` has `operationalState=OperationalUp` AND `provisioningState=Ready` |
| `HSIPerformanceExpectation` | `log:allOf` + 5× quantity conditions | DL ≥ 100 Mbps, UL ≥ 20 Mbps, latency < 25 ms, jitter < 3 ms, packet loss < 0.1 % |
| `HSIMonitoringExpectation` | opaque (`icm:ReportingExpectation`) | passes silently — not evaluated |

### Structural vs metric-driven conditions

- **Structural conditions** (DeliveryExpectation, log:match UNI state) depend on facts
  asserted *inside the expression Turtle itself*. They cannot be satisfied by posting
  metric observations.
- **Metric conditions** (the five quantity conditions) are satisfied by posting
  `met:Observation` records via `POST /intent/{id}/observation`.

To make the structural conditions pass in this demo, the expression Turtle includes
the relevant facts inline (see the demo expression below).

---

## Step 1 — Create the HSI intent

This expression includes inline structural facts so every condition is evaluable.
Metric values are supplied as observations in later steps.

```bash
HSI_INTENT=$(curl -s -X POST http://localhost:8000/tmf-api/intentManagement/v5/intent \
  -H "Content-Type: application/json" \
  -d '{
    "@type": "Intent",
    "name": "BBF HSI Service Demo",
    "lifecycleStatus": "ACKNOWLEDGED",
    "expression": {
      "@type": "TurtleExpression",
      "iri": "http://broadband-forum.org/Intent#HSIIntent",
      "expressionValue": "@prefix bbf:  <http://broadband-forum.org/Intent#> .\n@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n@prefix icm:  <http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/> .\n@prefix log:  <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .\n@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .\n@prefix met:  <http://tio.models.tmforum.org/tio/v3.6.0/MetricsAndObservations/> .\n@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n\nbbf:HSICompositeExpectation\n    log:allOf (\n        bbf:DeliveryCheck\n        bbf:UNICheck\n        bbf:PerformanceCheck\n    ) .\n\nbbf:DeliveryCheck  a icm:DeliveryExpectation ;\n    icm:target       bbf:SelectedUNIInterface ;\n    icm:deliveryType bbf:HSIService .\n\nbbf:SelectedUNIInterface\n    rdfs:member bbf:HSIServiceInstance .\n\nbbf:HSIServiceInstance  a bbf:HSIService .\n\nbbf:UNICheck\n    log:allOf (\n        bbf:UNIUpCondition\n        bbf:UNIReadyCondition\n    ) .\n\nbbf:UNIUpCondition\n    log:match ( bbf:SelectedUNIInterface\n                bbf:operationalState\n                bbf:OperationalUp ) .\n\nbbf:UNIReadyCondition\n    log:match ( bbf:SelectedUNIInterface\n                bbf:provisioningState\n                bbf:Ready ) .\n\nbbf:SelectedUNIInterface\n    bbf:operationalState  bbf:OperationalUp ;\n    bbf:provisioningState bbf:Ready .\n\nbbf:PerformanceCheck\n    log:allOf (\n        bbf:DL_Check\n        bbf:UL_Check\n        bbf:Lat_Check\n        bbf:Jit_Check\n        bbf:PL_Check\n    ) .\n\nbbf:DL_Check  a quan:quanatLeast ;\n    rdf:first bbf:DownstreamBandwidthMetric ;\n    rdf:rest  [ rdf:first bbf:DL_Bound ] .\nbbf:DL_Bound  rdf:value \"100\"^^xsd:decimal .\n\nbbf:UL_Check  a quan:quanatLeast ;\n    rdf:first bbf:UpstreamBandwidthMetric ;\n    rdf:rest  [ rdf:first bbf:UL_Bound ] .\nbbf:UL_Bound  rdf:value \"20\"^^xsd:decimal .\n\nbbf:Lat_Check  a quan:quansmaller ;\n    rdf:first bbf:LatencyMetric ;\n    rdf:rest  [ rdf:first bbf:Lat_Bound ] .\nbbf:Lat_Bound  rdf:value \"25\"^^xsd:decimal .\n\nbbf:Jit_Check  a quan:quansmaller ;\n    rdf:first bbf:JitterMetric ;\n    rdf:rest  [ rdf:first bbf:Jit_Bound ] .\nbbf:Jit_Bound  rdf:value \"3\"^^xsd:decimal .\n\nbbf:PL_Check  a quan:quansmaller ;\n    rdf:first bbf:PacketLossMetric ;\n    rdf:rest  [ rdf:first bbf:PL_Bound ] .\nbbf:PL_Bound  rdf:value \"0.1\"^^xsd:decimal .\n"
    }
  }')

echo "$HSI_INTENT" | python3 -m json.tool
INTENT_ID=$(echo "$HSI_INTENT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Intent ID: $INTENT_ID"
```

The API returns `201 Created` with the full intent body and a server-assigned `id`.
The evaluator fires immediately in the background.

---

## Step 2 — Check the initial report (Degraded — no observations)

The performance conditions reference metric URIs that have no observation records yet.

```bash
sleep 1   # wait for background evaluation task

curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent/$INTENT_ID/intentReport" \
  | python3 -m json.tool
```

> Reports are returned **newest first** (`ORDER BY DESC(creationDate)`).
> The first item in the array is always the latest evaluation result.

Expected `intentHandlingState`: **`Degraded`**

```json
[
  {
    "intentHandlingState": "Degraded",
    "intentHandlingReason": "Conditions not met: missing rdf:value; missing rdf:value; ...",
    ...
  }
]
```

The `intentHandlingReason` lists the five metric conditions that failed because no
`met:Observation` records exist for them yet.

> **If you see `"No intentHandlingState inferred from expression"`** — Fuseki contains a
> stale report written by an older version of the evaluator (pre-Phase 5). Stop the
> stack, wipe the volumes (`docker compose down -v`), and start from the beginning.
> The ordering fix in `intent_report_repository.py` ensures the newest report is
> always index `[0]`, but stale data from old code requires a clean restart.

---

## Step 3 — Submit observations (all conditions satisfied)

### Metric URIs

| Metric | URI |
|---|---|
| Downstream bandwidth | `http://broadband-forum.org/Intent#DownstreamBandwidthMetric` |
| Upstream bandwidth | `http://broadband-forum.org/Intent#UpstreamBandwidthMetric` |
| Latency | `http://broadband-forum.org/Intent#LatencyMetric` |
| Jitter | `http://broadband-forum.org/Intent#JitterMetric` |
| Packet loss | `http://broadband-forum.org/Intent#PacketLossMetric` |

### Submit all five (happy path)

Each `POST /observation` writes a `met:Observation` to the intent's observation graph
and immediately schedules a re-evaluation.

```bash
BASE="http://localhost:8000/tmf-api/intentManagement/v5/intent/$INTENT_ID/observation"
BBF="http://broadband-forum.org/Intent#"

# Downstream bandwidth: 150 Mbps  (threshold >= 100)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}DownstreamBandwidthMetric\", \"value\": 150.0}"

# Upstream bandwidth: 30 Mbps  (threshold >= 20)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}UpstreamBandwidthMetric\", \"value\": 30.0}"

# Latency: 8 ms  (threshold < 25)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}LatencyMetric\", \"value\": 8.0}"

# Jitter: 1.2 ms  (threshold < 3)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}JitterMetric\", \"value\": 1.2}"

# Packet loss: 0.02 %  (threshold < 0.1)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}PacketLossMetric\", \"value\": 0.02}"
```

---

## Step 4 — Verify Fulfilled state

```bash
sleep 1

curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent/$INTENT_ID/intentReport" \
  | python3 -m json.tool
```

Expected:

```json
[
  {
    "intentHandlingState": "Fulfilled",
    "intentHandlingReason": null,
    ...
  }
]
```

The latest report (first item) shows `Fulfilled`. The `handlerState` named graph in
Fuseki now contains RDF facts for every condition:

```
<intent> imo:intentHandlingState imo:Fulfilled .
<condition/0> imo:conditionPassed "true"^^xsd:boolean ;
              imo:observedValue   "150" ;
              imo:boundValue      "100" .
...
```

---

## Step 5 — Violate one condition (latency spike)

```bash
# Latency: 40 ms  (threshold < 25 — VIOLATED)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}LatencyMetric\", \"value\": 40.0}"

sleep 1

curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent/$INTENT_ID/intentReport" \
  | python3 -m json.tool
```

Expected:

```json
[
  {
    "intentHandlingState": "Degraded",
    "intentHandlingReason": "Conditions not met: 40 < 25: FAIL",
    ...
  }
]
```

The evaluator picks the **most recent** observation per metric. Submitting `value: 40.0`
replaces the effective latency reading; only the failing condition is listed in the
reason string.

---

## Step 6 — Recover (new within-threshold observation)

```bash
# Latency: 12 ms  (back within threshold)
curl -s -X POST "$BASE" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}LatencyMetric\", \"value\": 12.0}"

sleep 1

curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent/$INTENT_ID/intentReport" \
  | python3 -m json.tool
```

Expected: `"intentHandlingState": "Fulfilled"` — the system self-heals as soon as a
within-threshold observation arrives.

---

## Step 7 — Trigger evaluation without a new observation

If you want to force a re-evaluation without posting a metric (e.g., after patching the
expression or updating intent metadata):

```bash
curl -s -X PATCH \
  "http://localhost:8000/tmf-api/intentManagement/v5/intent/$INTENT_ID" \
  -H "Content-Type: application/merge-patch+json" \
  -d '{"description": "force re-evaluation"}'
```

Every `PATCH` schedules an evaluation cycle. The evaluator uses the same observation
graph as before, so the result will match the most recently submitted values.

---

## Step 8 — Inspect the handlerState graph directly (Fuseki)

The OODA working-memory graph is visible at:

```
http://localhost:3030/tmf921/data?graph=http://tmforum.org/api/v5/intents/<INTENT_ID>/handlerState
```

Or via SPARQL:

```sparql
PREFIX imo: <http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/>
PREFIX quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/>

SELECT ?type ?observed ?bound ?passed
WHERE {
  GRAPH <http://tmforum.org/api/v5/intents/INTENT_ID/handlerState> {
    ?intent imo:hasConditionResult ?c .
    ?c a ?type ;
       imo:conditionPassed ?passed .
    OPTIONAL { ?c imo:observedValue ?observed }
    OPTIONAL { ?c imo:boundValue ?bound }
  }
}
```

Replace `INTENT_ID` with the UUID from Step 1.

---

## Condition reference

| Condition | Type | Threshold | Pass example | Fail example |
|---|---|---|---|---|
| Downstream BW | `quan:quanatLeast` | ≥ 100 Mbps | 150 | 80 |
| Upstream BW | `quan:quanatLeast` | ≥ 20 Mbps | 30 | 15 |
| Latency | `quan:quansmaller` | < 25 ms | 8 | 40 |
| Jitter | `quan:quansmaller` | < 3 ms | 1.2 | 5 |
| Packet loss | `quan:quansmaller` | < 0.1 % | 0.02 | 0.2 |
| UNI operational | `log:match` | operationalState = OperationalUp | asserted in expression | remove triple |
| UNI ready | `log:match` | provisioningState = Ready | asserted in expression | remove triple |
| Service delivery | `icm:DeliveryExpectation` | target container has HSIService member | asserted in expression | remove rdfs:member |

---

## Using the seed data instead

If you prefer the full BBF intent from `seed_data/hsionlyintent_v0.5.ttl`, seed it:

```bash
.venv/bin/python seed_data/seed_intents.py
```

The seeder creates five intents including `BBF HSI Service Intent v0.5`. Find its ID:

```bash
curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent?name=BBF+HSI" \
  | python3 -m json.tool
```

Then submit observations using the same metric URIs as above. The structural conditions
(`UNIUpCondition`, `UNIReadyCondition`, `HSIServiceProvisioningExpectation`) use
`log:match` and `icm:DeliveryExpectation` respectively; they depend on triples already
present in the expression Turtle. The seed TTL as shipped does **not** assert the
concrete state triples (`bbf:SelectedUNIInterface bbf:operationalState bbf:OperationalUp`),
so those conditions will be `Degraded` until the expression is patched to include them
or until the intent handler receives external notification of provisioning completion.

---

## What to look at in the code

| Location | What it does |
|---|---|
| `src/handler/evaluator.py:evaluate_turtle_conditions()` | Parses TTL, runs pre-processing, walks the TIO tree |
| `src/handler/evaluator.py:_latest_observation_value()` | Picks the most-recent `met:Observation` by `obtainedAt` timestamp |
| `src/handler/evaluator.py:_eval_delivery_expectation()` | Checks `icm:target` container membership |
| `src/handler/evaluator.py:_eval_match()` | Checks a single `(s, p, o)` triple exists |
| `src/handler/evaluator.py:_eval_two_arg()` | Evaluates `quanatLeast`, `quansmaller`, etc. with Decimal precision |
| `src/handler/state_writer.py:build_handler_state_turtle()` | Serialises per-condition results to RDF |
| `src/handler/observation_store.py:write_observation()` | Appends a `met:Observation` to the observation graph |
| `src/handler/dispatcher.py:dispatch_evaluation()` | Orchestrates evaluate → write state → create report → notify |
| `src/api/routers/observation.py` | `POST /intent/{id}/observation` endpoint |
