"""
Seed the Access Domain TMF921 API with the BBF HSI intent.

The access domain manages UNI/ONT/OLT resources — its resource inventory
(pon_resource_onto.ttl + pon_resource_data.ttl) is loaded automatically at
startup via RESOURCE_DATA_DIR=BBF_access.  This script posts the HSI intent
that the handler will evaluate against those resources.

Usage:
    python seed_data/seed_access.py [--base-url http://localhost:8001]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

BASE = "/tmf-api/intentManagement/v5"
_SEED_DIR = Path(__file__).parent


async def wait_for_server(client: httpx.AsyncClient, retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            r = await client.get("/health")
            if r.status_code == 200:
                print("  Access domain API is UP")
                return
        except httpx.RequestError:
            pass
        if attempt < retries - 1:
            print(f"  Waiting for server ({attempt + 1}/{retries})…")
            await asyncio.sleep(2)
    print("ERROR: Access domain API did not become ready", file=sys.stderr)
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
        print(f"\nConnecting to access domain at {base_url}…")
        await wait_for_server(client)

        print("\nSeeding HSI Intent (North Region, subscriber BBF_SUB_12345)…")
        hsi_intent = {
            "@type": "Intent",
            "@baseType": "Intent",
            "name": "BBF HSI Service — North Region",
            "description": (
                "Provision HSI service on an available UNI in the North Region "
                "for subscriber BBF_SUB_12345. Handler selects a free UNI from "
                "the PON resource inventory and verifies performance conditions."
            ),
            "lifecycleStatus": "Acknowledged",
            "priority": "1",
            "context": "BBF-NorthRegion",
            "expression": {
                "@type": "TurtleExpression",
                "iri": "http://broadband-forum.org/Intent#HSIIntent",
                "expressionValue": (_SEED_DIR / "hsionlyintent_v0.5.ttl").read_text(
                    encoding="utf-8"
                ),
            },
        }
        intent = await post_resource(client, f"{BASE}/intent", hsi_intent, "HSI Intent")

        if intent.get("id"):
            print(f"\n  Intent ID: {intent['id']}")
            print(f"  GET {base_url}{BASE}/intent/{intent['id']}")
            print(f"  GET {base_url}{BASE}/intent/{intent['id']}/intentReport")
            print("\n  Post observations to trigger evaluation:")
            print(f"  POST {base_url}{BASE}/intent/{intent['id']}/observation")
            print("  Body: {{ \"metricName\": \"bbf:DownstreamBandwidthMetric\", \"value\": 150.0 }}")

        print("\nDone.\n")
        print("Next steps:")
        print("  1. POST observations to drive the performance conditions")
        print("  2. GET /intent/{id} to see lifecycleStatus and intentHandlingState")
        print("  3. Start the aggregation domain and run seed_aggregation.py for the")
        print("     F-interface ProbeIntent demo")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Access Domain with HSI intent")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Access domain API base URL (default: http://localhost:8001)",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.base_url))


if __name__ == "__main__":
    main()
