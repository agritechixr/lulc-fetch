"""Helpers for the API tests: check a response, wait for a background job."""

from __future__ import annotations

import os
import time

import pytest

JOB_TIMEOUT = float(os.environ.get("LULC_TEST_JOB_TIMEOUT", 900))


def ok(r, status: int = 200):
    """Assert an HTTP response status and return its JSON."""
    assert r.status_code == status, f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:2000]}"
    return r.json()


def wait(client, job: dict, timeout: float = JOB_TIMEOUT) -> dict:
    """Wait for a background job; fail with its log when it doesn't finish."""
    jid = job["id"]
    t0 = time.time()
    while True:
        j = ok(client.get(f"/api/jobs/{jid}"))
        if j["status"] in ("done", "error", "cancelled"):
            break
        if time.time() - t0 > timeout:
            client.post(f"/api/jobs/{jid}/cancel")
            pytest.fail(f"Job {j['title']} didn't finish in {timeout:.0f} s. Log:\n" + "\n".join(j.get("logs", [])[-30:]))
        time.sleep(0.25)
    assert j["status"] == "done", f"Job {j['title']} ended {j['status']}: {j.get('error')}\nLog:\n" + "\n".join(j.get("logs", [])[-30:])
    return j


def run(client, url: str, body: dict, timeout: float = JOB_TIMEOUT) -> dict:
    """POST a request that starts a background job, wait for it and return its result."""
    job = ok(client.post(url, json=body))
    return wait(client, job, timeout)["result"]
