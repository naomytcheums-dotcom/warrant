"""
Fires concurrent /authorize requests at a running warrant instance and
reports real p50/p95/p99 latency -- the numbers "runs at a scale that
makes the interesting problems genuinely hard" actually requires,
not a claim without measurement behind it.

Run: python loadtest/run.py --url http://localhost:8600 --concurrency 50 --requests 500
"""

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

REQUESTS = [
    {"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:search_docs"},
    {"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:escalate_to_human"},
    {"actor": "agent:rag-fastapi-assistant", "action": "call", "resource": "tool:delete_repository"},
    {"actor": "agent:code-fix-agent", "action": "call", "resource": "tool:delete_repository"},
]


def one_request(client, url, payload):
    start = time.perf_counter()
    response = client.post(f"{url}/authorize", json=payload, timeout=10)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, response.status_code


def percentile(values, pct):
    values = sorted(values)
    index = min(len(values) - 1, int(len(values) * pct))
    return values[index]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8600")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--requests", type=int, default=500)
    args = parser.parse_args()

    payloads = [REQUESTS[i % len(REQUESTS)] for i in range(args.requests)]

    # A connection-pooled, keep-alive client is not an optimization here --
    # without it, each request opens a fresh TCP connection, and connection
    # setup dominates the measurement instead of the server's actual work.
    # First version of this script used one-off httpx.post() calls and
    # measured 7.5s p50 against a server that, isolated, takes 0.3ms per
    # request -- the load generator was the bottleneck, not warrant.
    client = httpx.Client(limits=httpx.Limits(max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency))

    latencies = []
    errors = 0
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(one_request, client, args.url, p) for p in payloads]
        for future in as_completed(futures):
            elapsed_ms, status = future.result()
            if status != 200:
                errors += 1
            latencies.append(elapsed_ms)
    total_s = time.perf_counter() - start

    print(f"{args.requests} requests, concurrency={args.concurrency}")
    print(f"total time: {total_s:.2f}s -- {args.requests / total_s:.1f} req/s")
    print(f"errors: {errors}")
    print(f"p50: {percentile(latencies, 0.50):.1f}ms")
    print(f"p95: {percentile(latencies, 0.95):.1f}ms")
    print(f"p99: {percentile(latencies, 0.99):.1f}ms")
    print(f"max: {max(latencies):.1f}ms")
    print(f"mean: {statistics.mean(latencies):.1f}ms")


if __name__ == "__main__":
    main()
