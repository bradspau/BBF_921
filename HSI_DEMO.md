# HSI Intent Demo — End-to-End Walkthrough

This guide shows how to exercise the BBF High-Speed Internet (HSI) service intent
through the TMF921 API, verify that the intent handler evaluates conditions correctly,
and observe how the `intentHandlingState` transitions between `Fulfilled` and `Degraded`
as metric observations are submitted.

## Key concepts: lifecycleStatus vs intentHandlingState

Two different fields track two different things throughout this demo, don't confuse them:

- **`lifecycleStatus`** (on the Intent resource itself) — the TMF921 FSM state, always
  ALL-CAPS: `ACKNOWLEDGED`, `ACTIVE`, `FULFILLED`, `DEGRADED`, `SUSPENDED`, `TERMINATED`.
  This is *workflow* state, it says who owns the next action (the handler waiting for
  approval, the owner having accepted terms, etc.), not "are the conditions currently
  met right now".

- **`intentHandlingState`** (on each `IntentReport`, camelCase value: `Fulfilled` /
  `Degraded`) — a purely technical, continuously re-evaluated signal: "given the
  expression and the latest observations, do the conditions currently hold?" It's
  recomputed on every evaluation cycle and does not by itself change `lifecycleStatus`.

How they relate across the flows in this guide:

- **Steps 1–7 (plain Intent):** `lifecycleStatus` stays `ACKNOWLEDGED` the whole time.
  Only `intentHandlingState` flips between `Degraded` and `Fulfilled` as observations
  are submitted. The FSM doesn't move; only the technical assessment does.
- **Step 9 (ProbeIntent):** the handler auto-drives `lifecycleStatus` off the evaluation
  result — `Fulfilled` → PATCHes `lifecycleStatus` to `ACTIVE`; `Degraded` → PATCHes it
  to `TERMINATED`. This coupling is unique to ProbeIntent
  (`dispatcher.py:_try_probe_transition()`).
- **Step 10 (Best/Propose):** `intentHandlingState` goes `Degraded`, the handler proposes
  a best-effort bound by PATCHing `expressionValue`, but `lifecycleStatus` deliberately
  stays `ACKNOWLEDGED` — it waits for the owner to manually PATCH `lifecycleStatus: ACTIVE`
  once they accept the reduced terms (Step 10e).

See `docs/04-state-machine.md` for the full FSM transition rules.

---

Note that the TMF Ontology is loaded as default into a graph and can be queried by Jena terminal

  PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

  SELECT ?s ?p ?o
  WHERE {
    GRAPH <http://tmforum.org/api/v5/ontology> {
      ?s ?p ?o
    }
  }
  LIMIT 100

  or 

    curl -G http://localhost:3030/tmf921/sparql \
    --data-urlencode 'query=SELECT ?s ?p ?o WHERE { GRAPH <http://tmforum.org/api/v5/ontology> { ?s ?p ?o } } LIMIT 100'



> For the access domain demo using real PON resource inventory and UNI/CTAG allocation,
> see [`Access_HSI_Demo.md`](Access_HSI_Demo.md).

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
sudo docker compose --profile standalone up --build
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

Several steps below build request bodies with `jq` (readable multi-line Turtle via
heredoc, folded into JSON with correct escaping). Install it if you don't have it:
`sudo apt install jq` / `brew install jq`.

### Option A — Docker (recommended)

```bash
sudo docker compose --profile standalone up --build
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

The inline expression used in this demo defines a top-level `log:allOf` over three
expectations:

| Expectation | Evaluator | What it checks |
|---|---|---|
| `DeliveryCheck` | `icm:DeliveryExpectation` | UNI target container has a member typed `bbf:HSIService` |
| `UNICheck` | `log:allOf` + `log:match` | `SelectedUNIInterface` has `operationalState=OperationalUp` AND `provisioningState=Ready` |
| `PerformanceCheck` | `log:allOf` + 5× quantity conditions | DL ≥ 100 Mbps, UL ≥ 20 Mbps, latency < 25 ms, jitter < 3 ms, packet loss < 0.1 % |

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

The Turtle expression is assigned to a shell variable via a heredoc (readable, real
line breaks) and folded into the JSON body with `jq --arg`, which handles all escaping
for you. Requires `jq` (`sudo apt install jq` / `brew install jq` if you don't have it).

```bash
HSI_TURTLE=$(cat <<'TTL'
@prefix bbf:  <http://broadband-forum.org/Intent#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix icm:  <http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/> .
@prefix log:  <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

bbf:HSICompositeExpectation
    log:allOf (
        bbf:DeliveryCheck
        bbf:UNICheck
        bbf:PerformanceCheck
    ) .

bbf:DeliveryCheck  a icm:DeliveryExpectation ;
    icm:target       bbf:SelectedUNIInterface ;
    icm:deliveryType bbf:HSIService .

bbf:SelectedUNIInterface
    rdfs:member bbf:HSIServiceInstance .

bbf:HSIServiceInstance  a bbf:HSIService .

bbf:UNICheck
    log:allOf (
        bbf:UNIUpCondition
        bbf:UNIReadyCondition
    ) .

bbf:UNIUpCondition
    log:match ( bbf:SelectedUNIInterface
                bbf:operationalState
                bbf:OperationalUp ) .

bbf:UNIReadyCondition
    log:match ( bbf:SelectedUNIInterface
                bbf:provisioningState
                bbf:Ready ) .

bbf:SelectedUNIInterface
    bbf:operationalState  bbf:OperationalUp ;
    bbf:provisioningState bbf:Ready .

bbf:PerformanceCheck
    log:allOf (
        bbf:DL_Check
        bbf:UL_Check
        bbf:Lat_Check
        bbf:Jit_Check
        bbf:PL_Check
    ) .

bbf:DL_Check  quan:atLeast ( bbf:DownstreamBandwidthMetric
              [ rdf:value "100"^^xsd:decimal ] ) .

bbf:UL_Check  quan:atLeast ( bbf:UpstreamBandwidthMetric
              [ rdf:value "20"^^xsd:decimal ] ) .

bbf:Lat_Check  quan:smaller ( bbf:LatencyMetric
               [ rdf:value "25"^^xsd:decimal ] ) .

bbf:Jit_Check  quan:smaller ( bbf:JitterMetric
               [ rdf:value "3"^^xsd:decimal ] ) .

bbf:PL_Check  quan:smaller ( bbf:PacketLossMetric
              [ rdf:value "0.1"^^xsd:decimal ] ) .
TTL
)

HSI_INTENT=$(jq -n --arg turtle "$HSI_TURTLE" '{
    "@type": "Intent",
    "name": "BBF HSI Service Demo",
    "lifecycleStatus": "ACKNOWLEDGED",
    "expression": {
      "@type": "TurtleExpression",
      "iri": "http://broadband-forum.org/Intent#HSIIntent",
      "expressionValue": $turtle
    }
  }' | curl -s -X POST http://localhost:8000/tmf-api/intentManagement/v5/intent \
  -H "Content-Type: application/json" \
  -d @-)

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

You can also use the Jena Fuseki Sparql console to query for the icm:result for each of the conditions and the intent itself.
  
  ```bash
  cat <<EOF
  PREFIX imo: <http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/>
  PREFIX icm: <http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/>

  SELECT ?condition ?type ?passed ?icmResult
  WHERE {
    GRAPH <http://tmforum.org/api/v5/intents/$INTENT_ID/handlerState> {
      <http://tmforum.org/api/v5/intents/$INTENT_ID> imo:hasConditionResult ?condition .
      ?condition a ?type ;
                 imo:conditionPassed ?passed .
      OPTIONAL { ?condition icm:result ?icmResult }
    }
  }
  EOF
  ```
  Copy the output (with the real UUID inline) and paste it into the fuseki Query tab, then click Run query.

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

> **All steps must run in the same shell session** so that `$INTENT_ID` is in scope.
> If you open a new terminal, re-export it first:
> ```bash
> INTENT_ID="<id from step 1 output>"
> ```

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
sleep 2

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

### Understanding Fuseki datasets vs named graphs

The Fuseki UI sidebar shows two **datasets**: `tmf921` and `tmf921-eval`.
These are top-level containers — not the graphs themselves.

Every intent, report, observation set, and handlerState is a separate **named graph**
stored *inside* the `tmf921` dataset, identified by a URI. You cannot see them listed
in the Fuseki sidebar; you discover them by querying the dataset.

| What you see in the sidebar | What it contains |
|---|---|
| `tmf921` | All API named graphs — intents, reports, observations, handlerState, hubs |
| `tmf921-eval` | Separate in-memory dataset — not used at runtime; ignore it |

Named graph URIs follow this pattern (base: `http://tmforum.org/api/v5`):

| Named graph | Contents |
|---|---|
| `.../intents/{uuid}` | Intent resource + expression Turtle |
| `.../intents/{uuid}/handlerState` | Per-condition evaluation facts (OODA working memory) |
| `.../intents/{uuid}/observations` | Metric observation records |
| `.../reports/{uuid}` | IntentReport from each evaluation cycle |
| `.../hubs` | Hub subscription records |

---

### 8a — Discover all named graphs in the dataset

Run this in the Fuseki UI (`http://localhost:3030` → select `tmf921` → Query tab),
or from the shell (see 8c below):

```sparql
SELECT DISTINCT ?g WHERE { GRAPH ?g { } }
```

This lists every named graph that currently exists in the `tmf921` dataset.

---

### 8b — Fetch raw Turtle via Graph Store Protocol (shell)

The simplest way to dump a named graph's full contents:

```bash
curl -s \
  "http://localhost:3030/tmf921/data?graph=http://tmforum.org/api/v5/intents/$INTENT_ID/handlerState"
```

Replace `handlerState` with `observations` or omit the suffix entirely to fetch the
intent's own named graph.

---

### 8c — Query via SPARQL POST (shell)

Run SPARQL directly from the terminal — no browser needed. The SPARQL endpoint for the
`tmf921` dataset is `http://localhost:3030/tmf921/sparql`.

**List all named graphs:**

```bash
curl -s -X POST "http://localhost:3030/tmf921/sparql" \
  -H "Content-Type: application/sparql-query" \
  -d "SELECT DISTINCT ?g WHERE { GRAPH ?g { } }" \
  | python3 -m json.tool
```

**Query the handlerState conditions:**

Two-argument conditions (`atLeast`, `smaller`, etc.) use `imo:boundValue`.
Range conditions (`quaninRange`) use `imo:lowerBound` + `imo:upperBound` instead.
The query below handles both — null columns simply won't appear in the binding:

```bash
curl -s -X POST "http://localhost:3030/tmf921/sparql" \
  -H "Content-Type: application/sparql-query" \
  -d "
PREFIX imo: <http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/>

SELECT ?type ?observed ?bound ?lower ?upper ?passed
WHERE {
  GRAPH <http://tmforum.org/api/v5/intents/$INTENT_ID/handlerState> {
    ?intent imo:hasConditionResult ?c .
    ?c a ?type ;
       imo:conditionPassed ?passed .
    OPTIONAL { ?c imo:observedValue ?observed }
    OPTIONAL { ?c imo:boundValue    ?bound }
    OPTIONAL { ?c imo:lowerBound    ?lower }
    OPTIONAL { ?c imo:upperBound    ?upper }
  }
}" | python3 -m json.tool
```

Expected output after Step 4 (Fulfilled state) — two-argument conditions:

```json
{
  "results": {
    "bindings": [
      {
        "type":     { "value": "...atLeast" },
        "observed": { "value": "150" },
        "bound":    { "value": "100" },
        "passed":   { "value": "true" }
      },
      ...
    ]
  }
}
```

---

### 8d — Query via the Fuseki UI

Open `http://localhost:3030`, select the **`tmf921`** dataset, choose the **Query** tab.

Run this shell command to generate a ready-to-paste query with your UUID substituted:

```bash
cat <<EOF
PREFIX imo: <http://tio.models.tmforum.org/tio/v3.6.0/IntentManagementOntology/>

SELECT ?type ?observed ?bound ?lower ?upper ?passed
WHERE {
  GRAPH <http://tmforum.org/api/v5/intents/$INTENT_ID/handlerState> {
    ?intent imo:hasConditionResult ?c .
    ?c a ?type ;
       imo:conditionPassed ?passed .
    OPTIONAL { ?c imo:observedValue ?observed }
    OPTIONAL { ?c imo:boundValue    ?bound }
    OPTIONAL { ?c imo:lowerBound    ?lower }
    OPTIONAL { ?c imo:upperBound    ?upper }
  }
}
EOF
```

Copy the output (with the real UUID inline) and paste it into the Fuseki Query tab,
then click **Run query**.

---

## Condition reference

| Condition | Type | Threshold | Pass example | Fail example |
|---|---|---|---|---|
| Downstream BW | `quan:atLeast` | ≥ 100 Mbps | 150 | 80 |
| Upstream BW | `quan:atLeast` | ≥ 20 Mbps | 30 | 15 |
| Latency | `quan:smaller` | < 25 ms | 8 | 40 |
| Jitter | `quan:smaller` | < 3 ms | 1.2 | 5 |
| Packet loss | `quan:smaller` | < 0.1 % | 0.02 | 0.2 |
| UNI operational | `log:match` | operationalState = OperationalUp | asserted in expression | remove triple |
| UNI ready | `log:match` | provisioningState = Ready | asserted in expression | remove triple |
| Service delivery | `icm:DeliveryExpectation` | target container has HSIService member | asserted in expression | remove rdfs:member |

---

## Using the seed scripts

For the default single-domain mode, seed four generic sample intents and one
IntentSpecification:

```bash
python seed_data/seed_intents.py
```

For the access domain HSI intent and the F-interface demo, see [`Access_HSI_Demo.md`](Access_HSI_Demo.md).

---

## Step 9 — Flow 1: ProbeIntent (capability probe)

The ProbeIntent flow lets a client ask "can you satisfy these terms?" without committing
to a full intent. The handler evaluates the ProbeIntent expression and auto-transitions it:

- `Fulfilled` → `ACTIVE` — the handler accepts the proposed terms
- `Degraded` → `TERMINATED` — the handler cannot satisfy the terms

### What you implement

You are responsible for three things in the `POST /intent` request:

1. **`@type: "ProbeIntent"`** — tells the API this is a capability probe, not a binding intent
2. **`intentRelationship`** — an array entry with the parent intent's `id` and `"relationshipType": "relatesTo"`
3. **`expression`** — must be `@type: "TurtleExpression"` with a `expressionValue` Turtle string defining the terms you want to probe

The handler does the rest automatically: it evaluates the expression immediately after creation
and patches `lifecycleStatus` to `ACTIVE` or `TERMINATED` based on the result.

> **Important:** Use `TurtleExpression` — a `JsonLdExpression` cannot be evaluated by the handler
> and will always result in `TERMINATED`.

> **Important:** The ProbeIntent has its own observation graph. If your expression references
> metric URIs, post observations to `POST /intent/{probeId}/observation` — not the parent intent's ID.

This example uses structural `log:match` facts asserted inline so the result is
deterministic without requiring observations.

### 9a — Probe that passes (structural conditions satisfied inline)

```bash
PROBE_PASS_TURTLE=$(cat <<'TTL'
@prefix bbf: <http://broadband-forum.org/Intent#> .
@prefix log: <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .

bbf:ProbeCheck log:allOf ( bbf:UNICheck ) .
bbf:UNICheck log:match ( bbf:SelectedUNI bbf:operationalState bbf:OperationalUp ) .
bbf:SelectedUNI bbf:operationalState bbf:OperationalUp .
TTL
)

PROBE_PASS=$(jq -n \
  --arg intentId "$INTENT_ID" \
  --arg turtle "$PROBE_PASS_TURTLE" \
  '{
    "@type": "ProbeIntent",
    "name": "HSI Capability Probe — pass",
    "intentRelationship": [{
      "@type": "IntentRelationship",
      "id": $intentId,
      "relationshipType": "relatesTo",
      "referredType": "Intent"
    }],
    "expression": {
      "@type": "TurtleExpression",
      "iri": "http://broadband-forum.org/Intent#ProbePass",
      "expressionValue": $turtle
    }
  }' | curl -s -X POST http://localhost:8000/tmf-api/intentManagement/v5/intent \
  -H "Content-Type: application/json" \
  -d @-)

echo "$PROBE_PASS" | python3 -m json.tool
PROBE_PASS_ID=$(echo "$PROBE_PASS" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "ProbeIntent ID: $PROBE_PASS_ID"
```

Wait a moment then check the ProbeIntent's status — the handler should have auto-transitioned it:

```bash
sleep 1
curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent/$PROBE_PASS_ID" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Status:', d['lifecycleStatus'])"
```

Expected: **`ACTIVE`** — the handler accepted the terms.

### 9b — Probe that fails (structural condition absent)

```bash
PROBE_FAIL_TURTLE=$(cat <<'TTL'
@prefix bbf: <http://broadband-forum.org/Intent#> .
@prefix log: <http://tio.models.tmforum.org/tio/v3.6.0/LogicalOperators/> .

bbf:ProbeCheck log:allOf ( bbf:UNICheck ) .
bbf:UNICheck log:match ( bbf:SelectedUNI bbf:operationalState bbf:Shutdown ) .
TTL
)

PROBE_FAIL=$(jq -n \
  --arg intentId "$INTENT_ID" \
  --arg turtle "$PROBE_FAIL_TURTLE" \
  '{
    "@type": "ProbeIntent",
    "name": "HSI Capability Probe — fail",
    "intentRelationship": [{
      "@type": "IntentRelationship",
      "id": $intentId,
      "relationshipType": "relatesTo",
      "referredType": "Intent"
    }],
    "expression": {
      "@type": "TurtleExpression",
      "iri": "http://broadband-forum.org/Intent#ProbeFail",
      "expressionValue": $turtle
    }
  }' | curl -s -X POST http://localhost:8000/tmf-api/intentManagement/v5/intent \
  -H "Content-Type: application/json" \
  -d @-)

PROBE_FAIL_ID=$(echo "$PROBE_FAIL" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "ProbeIntent ID: $PROBE_FAIL_ID"

sleep 1
curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent/$PROBE_FAIL_ID" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Status:', d['lifecycleStatus'])"
```

Expected: **`TERMINATED`** — the required triple (`bbf:Shutdown`) was not found in the expression graph.

---

## Step 10 — Flow 3: Best/Propose (handler proposes achievable bounds)

When an intent's `TurtleExpression` evaluates as Degraded, the handler automatically:
1. Substitutes best-effort bound values into the failed conditions' `expressionValue`
2. PATCHes the intent with the updated expression and fires `intentAttributeValueChangeEvent`
3. Waits for the owner to inspect and approve by PATCHing `lifecycleStatus: ACTIVE`

### What you implement

**Owner (you):** Set the desired bounds in the `expressionValue` Turtle when you `POST /intent`.
These are your "ask" — the best outcome you want. The handler reads them, evaluates against
real observations, and proposes what it can actually deliver.

**Operator (server config):** Optionally set `HANDLER_LIMITS_JSON` as a fallback for conditions
that have no observations yet. Without this, Flow 3 can only propose bounds when an observation
exists. If neither observations nor a matching limits entry are available for a failed condition,
the handler makes no PATCH and the intent stays Degraded with the original expression unchanged.

**Handler (automatic):** No additional configuration is needed to trigger Flow 3. It fires
automatically after every Degraded evaluation on a `TurtleExpression` intent in `ACKNOWLEDGED`
or `ACTIVE` state.

The best-effort bound comes from the **observed value** in the last evaluation cycle
(primary), falling back to **`HANDLER_LIMITS_JSON`** if no observation exists.

### 10a — Set operator limits (optional fallback)

To demonstrate the limits fallback, set `HANDLER_LIMITS_JSON` before starting the API.
With Docker Compose, add it to the `app` service environment in `docker-compose.yml`:

```yaml
environment:
  HANDLER_LIMITS_JSON: '{"atLeast": 80.0, "smaller": 30.0}'
```

Or export it before starting the local dev server:

```bash
export HANDLER_LIMITS_JSON='{"atLeast": 80.0, "smaller": 30.0}'
.venv/bin/uvicorn src.main:app --reload
```

### 10b — Create an intent with unreachable bounds

Post an intent whose bounds cannot be met by any observation. Then submit an
observation so the evaluator knows the actual measured value.

```bash
STRICT_TURTLE=$(cat <<'TTL'
@prefix bbf: <http://broadband-forum.org/Intent#> .
@prefix quan: <http://tio.models.tmforum.org/tio/v3.6.0/QuantityOntology/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

bbf:DL_Check  quan:atLeast ( bbf:DownstreamBandwidthMetric
              [ rdf:value "500"^^xsd:decimal ] ) .
TTL
)

STRICT=$(jq -n --arg turtle "$STRICT_TURTLE" '{
    "@type": "Intent",
    "name": "HSI Flow3 Demo — strict bounds",
    "expression": {
      "@type": "TurtleExpression",
      "iri": "http://broadband-forum.org/Intent#StrictBW",
      "expressionValue": $turtle
    }
  }' | curl -s -X POST http://localhost:8000/tmf-api/intentManagement/v5/intent \
  -H "Content-Type: application/json" \
  -d @-)

echo "$STRICT" | python3 -m json.tool
STRICT_ID=$(echo "$STRICT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Strict intent ID: $STRICT_ID"
```

### 10c — Submit an observation to trigger evaluation

```bash
BASE2="http://localhost:8000/tmf-api/intentManagement/v5/intent/$STRICT_ID/observation"
BBF="http://broadband-forum.org/Intent#"

# System can only deliver 150 Mbps — well below the 500 Mbps bound
curl -s -X POST "$BASE2" -H "Content-Type: application/json" \
  -d "{\"metricUri\": \"${BBF}DownstreamBandwidthMetric\", \"value\": 150.0}"

sleep 2
```

### 10d — Inspect the handler's best-effort proposal

The handler evaluated `150 ≥ 500 → Degraded`, then substituted `observed=150` as the
new bound and PATCHed the intent's expression:

```bash
curl -s "http://localhost:8000/tmf-api/intentManagement/v5/intent/$STRICT_ID" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
expr = d.get('expression', {}).get('expressionValue', '')
print('lifecycleStatus:', d.get('lifecycleStatus'))
print()
print('Updated expressionValue (look for 150 replacing 500):')
print(expr[:600])
"
```

The `expressionValue` now contains `rdf:value "150"^^xsd:decimal` in place of `"500"`.
The intent remains in `ACKNOWLEDGED` state — the handler waits for the owner to accept.

### 10e — Owner approves the proposal

```bash
curl -s -X PATCH \
  "http://localhost:8000/tmf-api/intentManagement/v5/intent/$STRICT_ID" \
  -H "Content-Type: application/merge-patch+json" \
  -d '{"lifecycleStatus": "ACTIVE"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Status:', d['lifecycleStatus'])"
```

Expected: **`ACTIVE`** — the owner has accepted the best-effort terms. The next
evaluation cycle will confirm the bound is now achievable and produce a `Fulfilled` report.

---

## What to look at in the code

| Location | What it does |
|---|---|
| `src/handler/evaluator.py:evaluate_turtle_conditions()` | Parses TTL, runs pre-processing, walks the TIO tree |
| `src/handler/evaluator.py:_latest_observation_value()` | Picks the most-recent `met:Observation` by `obtainedAt` timestamp |
| `src/handler/evaluator.py:_eval_delivery_expectation()` | Checks `icm:target` container membership |
| `src/handler/evaluator.py:_eval_match()` | Checks a single `(s, p, o)` triple exists |
| `src/handler/evaluator.py:_eval_two_arg()` | Evaluates `atLeast`, `smaller`, etc. with Decimal precision |
| `src/handler/state_writer.py:build_handler_state_turtle()` | Serialises per-condition results to RDF |
| `src/handler/observation_store.py:write_observation()` | Appends a `met:Observation` to the observation graph |
| `src/handler/dispatcher.py:dispatch_evaluation()` | Orchestrates evaluate → write state → create report → notify |
| `src/handler/dispatcher.py:_try_probe_transition()` | Flow 1: auto-transitions ProbeIntent to ACTIVE or TERMINATED |
| `src/handler/dispatcher.py:_try_best_propose()` | Flow 3: substitutes best-effort bounds, PATCHes expressionValue |
| `src/handler/limits.py:apply_best_effort_bounds()` | Parses Turtle, finds failed quantity nodes, updates bound rdf:value |
| `src/api/routers/observation.py` | `POST /intent/{id}/observation` endpoint |
