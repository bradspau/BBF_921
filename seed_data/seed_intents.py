"""
Seed the TMF921 Intent Management API with sample data.

Usage:
    python seed_data/seed_intents.py [--base-url http://localhost:8000]

Requires the API server (and Fuseki) to be running.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

BASE = "/tmf-api/intentManagement/v5"

_SEED_DIR = Path(__file__).parent

INTENTS: list[dict] = [
    {
        "@type": "Intent",
        "@baseType": "Intent",
        "name": "High-Availability Network Slice",
        "description": "Ensure 99.99% availability for network slice A1.",
        "lifecycleStatus": "Active",
        "priority": "1",
        "context": "5G-Core",
        "expression": {
            "@type": "JsonLdExpression",
            "iri": "https://tmforum.org/tio/v3.2.1/AvailabilityIntent",
            "expressionValue": {
                "@context": "https://tmforum.org/intent/v5",
                "availability": "99.99%",
                "measurementInterval": "PT5M",
            },
        },
    },
    {
        "@type": "Intent",
        "@baseType": "Intent",
        "name": "Low-Latency Edge Service",
        "description": "Keep RTT below 10ms for edge compute nodes.",
        "lifecycleStatus": "Active",
        "priority": "2",
        "context": "EdgeCompute",
        "expression": {
            "@type": "TurtleExpression",
            "iri": "https://tmforum.org/tio/v3.2.1/LatencyExpectation",
            "expressionValue": (
                "@prefix tio: <https://tmforum.org/tio/v3.2.1/> .\n"
                "[] a tio:DeliveryExpectation ;\n"
                "   tio:subject <urn:resource:edge-node-1> ;\n"
                "   tio:condition [ tio:metric tio:Latency ; tio:lessThan 10 ] ."
            ),
        },
    },
    {
        "@type": "ProbeIntent",
        "@baseType": "ProbeIntent",
        "name": "Bandwidth Probe — Backbone Link B2",
        "description": "Probe available bandwidth on backbone link B2.",
        "lifecycleStatus": "Active",
        "priority": "3",
        "context": "Backbone",
        "expression": {
            "@type": "JsonLdExpression",
            "iri": "https://tmforum.org/tio/v3.2.1/BandwidthProbe",
            "expressionValue": {
                "@context": "https://tmforum.org/intent/v5",
                "probe": "bandwidth",
                "link": "B2",
            },
        },
    },
    {
        "@type": "Intent",
        "@baseType": "Intent",
        "name": "Security Compliance — Zone C",
        "description": "Enforce TLS 1.3 on all interfaces in security zone C.",
        "lifecycleStatus": "Acknowledged",
        "priority": "1",
        "context": "SecurityZone-C",
        "expression": {
            "@type": "JsonLdExpression",
            "iri": "https://tmforum.org/tio/v3.2.1/SecurityCompliance",
            "expressionValue": {
                "@context": "https://tmforum.org/intent/v5",
                "tlsVersion": "1.3",
                "zone": "C",
            },
        },
    },
    {
        "@type": "Intent",
        "@baseType": "Intent",
        "name": "BBF HSI Service Intent v0.5",
        "description": "Provision HSI service on a UNI with BBF subscriber association — North Region, subscriber BBF_SUB_12345.",
        "lifecycleStatus": "Acknowledged",
        "priority": "1",
        "context": "BBF-NorthRegion",
        "expression": {
            "@type": "TurtleExpression",
            "iri": "http://broadband-forum.org/Intent#HSIIntent",
            "expressionValue": (_SEED_DIR / "hsionlyintent_v0.5.ttl").read_text(encoding="utf-8"),
        },
    },
]

INTENT_SPEC: dict = {
    "@type": "IntentSpecification",
    "@baseType": "IntentSpecification",
    "name": "Network Slice Availability Spec v1",
    "description": "Specification for availability-based network slice intents.",
    "lifecycleStatus": "ACTIVE",
    "version": "1.0.0",
}


async def wait_for_server(client: httpx.AsyncClient, retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            r = await client.get("/health")
            if r.status_code == 200:
                print("  API server is UP")
                return
        except httpx.RequestError:
            pass
        if attempt < retries - 1:
            print(f"  Waiting for server ({attempt + 1}/{retries})…")
            await asyncio.sleep(2)
    print("ERROR: API server did not become ready", file=sys.stderr)
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


async def seed(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        print("Waiting for API server…")
        await wait_for_server(client)

        print("\nSeeding IntentSpecification…")
        await post_resource(client, f"{BASE}/intentSpecification", INTENT_SPEC, "IntentSpec")

        print("\nSeeding Intents…")
        for intent in INTENTS:
            await post_resource(client, f"{BASE}/intent", intent, intent["@type"])

        print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed TMF921 API with sample data")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.base_url))


if __name__ == "__main__":
    main()
