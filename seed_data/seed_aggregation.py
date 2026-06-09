"""
Seed the Aggregation Domain TMF921 API and demonstrate the F-interface.

The aggregation domain acts as an intent owner posting ProbeIntents to the
access domain to check resource availability before committing a service.

F-interface flow demonstrated:
  1. Aggregation domain registers a hub subscription on the access domain
     so it receives intentStatusChangeEvents.
  2. Aggregation domain posts a ProbeIntent to the access domain expressing
     the HSI performance terms it requires.
  3. Access domain evaluates the probe against its resource inventory.
     - Fulfilled → access domain auto-transitions probe to ACTIVE.
     - Degraded  → access domain auto-transitions probe to TERMINATED.
  4. Aggregation domain polls or receives event confirming the result.
  5. If probe passed: aggregation domain posts the full HSI Intent to the
     access domain to request service provisioning.

Usage:
    python seed_data/seed_aggregation.py \\
        [--agg-url  http://localhost:8000] \\
        [--access-url http://localhost:8001]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

BASE = "/tmf-api/intentManagement/v5"
_SEED_DIR = Path(__file__).parent

# Minimal ProbeIntent Turtle — asks "does the access domain have a free UNI for HSI?"
# Uses DeliveryExpectation + set constructor: passes if ≥1 free operational UNI exists.
# No runtime metric observation needed — resolves purely against the resource inventory.
_PROBE_TURTLE = """\
@prefix bbf:  <http://broadband-forum.org/Intent#> .
@prefix pon:  <http://broadband-forum.org/ont/pon-resource#> .
@prefix icm:  <http://tio.models.tmforum.org/tio/v3.6.0/IntentCommonModel/> .
@prefix set:  <http://tio.models.tmforum.org/tio/v3.6.0/SetOperators/> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

# Does the access domain have at least one free, operational UNI available for HSI?
bbf:ProbeExpectation  a icm:DeliveryExpectation ;
    icm:target       bbf:ProbeSelectedUNI ;
    icm:deliveryType pon:HSIService ;
    icm:chooseFrom   bbf:ProbeAvailableUNIs .

bbf:ProbeSelectedUNI  a icm:Target .

bbf:ProbeAvailableUNIs  a icm:Target ;
    set:resourcesOfType              pon:UNIPort ;
    set:resourcesWithPropertyObject ( pon:inUse            false ) ;
    set:resourcesWithPropertyObject ( pon:operationalState pon:Up ) .
"""


async def wait_for_server(client: httpx.AsyncClient, label: str, retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            r = await client.get("/health")
            if r.status_code == 200:
                print(f"  {label} is UP")
                return
        except httpx.RequestError:
            pass
        if attempt < retries - 1:
            print(f"  Waiting for {label} ({attempt + 1}/{retries})…")
            await asyncio.sleep(2)
    print(f"ERROR: {label} did not become ready", file=sys.stderr)
    sys.exit(1)


async def post_resource(
    client: httpx.AsyncClient,
    path: str,
    payload: dict,
    label: str,
) -> dict:
    r = await client.post(path, json=payload)
    if r.status_code in (200, 201):
        created = r.json()
        print(f"  Created {label}: {created.get('id', '?')} — {created.get('name', '?')}")
        return created
    print(f"  WARN: {label} POST {path} → {r.status_code}: {r.text[:200]}", file=sys.stderr)
    return {}


async def seed(agg_url: str, access_url: str) -> None:

    # ── Step 1: Verify both domains are up ──────────────────────────────────
    async with httpx.AsyncClient(base_url=agg_url, timeout=30.0) as agg:
        print(f"\nConnecting to aggregation domain at {agg_url}…")
        await wait_for_server(agg, "Aggregation domain")

    async with httpx.AsyncClient(base_url=access_url, timeout=30.0) as acc:
        print(f"Connecting to access domain at {access_url}…")
        await wait_for_server(acc, "Access domain")

    # ── Step 2: Register hub on access domain so aggregation gets events ────
    async with httpx.AsyncClient(base_url=access_url, timeout=30.0) as acc:
        print(f"\nRegistering hub subscription on access domain ({access_url})…")
        hub = await post_resource(
            acc,
            f"{BASE}/hub",
            {
                "callback": f"{agg_url}/tmf-api/intentManagement/v5/listener",
                "query": "eventType=intentStatusChangeEvent",
            },
            "Hub subscription",
        )

        # ── Step 3: Post ProbeIntent to access domain ────────────────────────
        print("\nPosting ProbeIntent to access domain (F-interface)…")
        probe = await post_resource(
            acc,
            f"{BASE}/intent",
            {
                "@type": "ProbeIntent",
                "@baseType": "ProbeIntent",
                "name": "AGG→ACCESS: HSI capability probe",
                "description": (
                    "Aggregation domain probing access domain: "
                    "can you deliver >= 100 Mbps HSI with an available UNI?"
                ),
                "lifecycleStatus": "Acknowledged",
                "priority": "1",
                "context": "F-interface",
                "expression": {
                    "@type": "TurtleExpression",
                    "iri": "http://broadband-forum.org/Intent#HSIProbe",
                    "expressionValue": _PROBE_TURTLE,
                },
            },
            "ProbeIntent",
        )

        probe_id = probe.get("id")
        if not probe_id:
            print("ERROR: ProbeIntent creation failed", file=sys.stderr)
            sys.exit(1)

        # ── Step 4: Poll for probe result ────────────────────────────────────
        print(f"\nPolling access domain for probe result (intent/{probe_id})…")
        result_status = None
        for attempt in range(15):
            await asyncio.sleep(2)
            r = await acc.get(f"{BASE}/intent/{probe_id}")
            if r.status_code == 200:
                data = r.json()
                status = data.get("lifecycleStatus", "")
                if status in ("ACTIVE", "TERMINATED"):
                    result_status = status
                    print(f"  Probe result: lifecycleStatus = {status}")
                    break
                print(f"  Attempt {attempt + 1}: still {status}…")

        if result_status is None:
            print("  Probe did not resolve within timeout — check access domain logs")

    # ── Step 5: Post full HSI Intent if probe passed ─────────────────────────
    if result_status == "ACTIVE":
        async with httpx.AsyncClient(base_url=access_url, timeout=30.0) as acc:
            print("\nProbe passed — posting full HSI Intent to access domain…")
            intent = await post_resource(
                acc,
                f"{BASE}/intent",
                {
                    "@type": "Intent",
                    "@baseType": "Intent",
                    "name": "AGG→ACCESS: HSI service request — BBF_SUB_12345",
                    "description": (
                        "Full HSI service intent submitted by aggregation domain "
                        "following successful ProbeIntent. "
                        "Subscriber: BBF_SUB_12345, Region: North."
                    ),
                    "lifecycleStatus": "Acknowledged",
                    "priority": "1",
                    "context": "BBF-NorthRegion",
                    "expression": {
                        "@type": "TurtleExpression",
                        "iri": "http://broadband-forum.org/Intent#HSIIntent",
                        "expressionValue": (
                            _SEED_DIR / "hsionlyintent_v0.5.ttl"
                        ).read_text(encoding="utf-8"),
                    },
                },
                "HSI Intent",
            )
            intent_id = intent.get("id")
            if intent_id:
                print(f"\n  HSI Intent created: {intent_id}")
                print(f"  Monitor: GET {access_url}{BASE}/intent/{intent_id}")
                print(f"  Reports: GET {access_url}{BASE}/intent/{intent_id}/intentReport")
                print("\n  Post observations to the access domain to drive the OODA loop:")
                print(f"  POST {access_url}{BASE}/intent/{intent_id}/observation")

    elif result_status == "TERMINATED":
        print("\nProbe failed — access domain cannot satisfy the requested terms.")
        print("  Options:")
        print("  - Inspect the IntentReport on the access domain for which conditions failed")
        print(f"    GET {access_url}{BASE}/intent/{probe_id}/intentReport")
        print("  - Relax the performance bounds and retry the probe")
        print("  - Check HANDLER_LIMITS_JSON on the access domain for Flow 3 Best/Propose")

    print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed aggregation domain and run F-interface ProbeIntent demo"
    )
    parser.add_argument(
        "--agg-url",
        default="http://localhost:8000",
        help="Aggregation domain API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--access-url",
        default="http://localhost:8001",
        help="Access domain API base URL (default: http://localhost:8001)",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.agg_url, args.access_url))


if __name__ == "__main__":
    main()
