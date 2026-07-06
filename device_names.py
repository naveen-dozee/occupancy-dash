"""Resolve device display names from devicesdb collectors."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

DEFAULT_DEVICESDB = "http://devicesdb-sit.dozee.int"
BATCH_SIZE = 100


def format_device_name(prefix: Optional[str], sequence: Optional[int | str]) -> Optional[str]:
    if prefix and sequence is not None:
        return f"{prefix}-{sequence}"
    return None


def _fetch_collector(
    devicesdb_endpoint: str,
    device_id: str,
) -> Optional[dict]:
    query = urllib.parse.urlencode(
        {
            "CollectorId": f"eq.{device_id}",
            "select": "CollectorId,Prefix,Sequence",
        }
    )
    url = f"{devicesdb_endpoint.rstrip('/')}/collectors?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, list) and data:
        return data[0]
    return None


def _fetch_collectors(
    devicesdb_endpoint: str,
    device_ids: list[str],
) -> list[dict]:
    if not device_ids:
        return []

    query = urllib.parse.urlencode(
        {
            "CollectorId": f"in.({','.join(device_ids)})",
            "select": "CollectorId,Prefix,Sequence",
        }
    )
    url = f"{devicesdb_endpoint.rstrip('/')}/collectors?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def lookup_device_names(
    device_ids: list[str],
    *,
    devicesdb_endpoint: Optional[str] = None,
) -> dict[str, str]:
    endpoint = devicesdb_endpoint or DEFAULT_DEVICESDB
    unique_ids = list(dict.fromkeys(device_id for device_id in device_ids if device_id))
    names: dict[str, str] = {}

    for offset in range(0, len(unique_ids), BATCH_SIZE):
        batch = unique_ids[offset: offset + BATCH_SIZE]
        batch_names: dict[str, str] = {}
        try:
            collectors = _fetch_collectors(endpoint, batch)
            for collector in collectors:
                collector_id = collector.get("CollectorId")
                name = format_device_name(collector.get("Prefix"), collector.get("Sequence"))
                if collector_id and name:
                    batch_names[collector_id] = name
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            collectors = []

        missing = [device_id for device_id in batch if device_id not in batch_names]
        for device_id in missing:
            try:
                collector = _fetch_collector(endpoint, device_id)
                if collector:
                    name = format_device_name(collector.get("Prefix"), collector.get("Sequence"))
                    if name:
                        batch_names[device_id] = name
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                continue

        names.update(batch_names)

    return names


def enrich_records(
    records: list[dict],
    *,
    devicesdb_endpoint: Optional[str] = None,
) -> list[dict]:
    if not records:
        return records

    device_ids = [record.get("DeviceId", "") for record in records if record.get("DeviceId")]
    names = lookup_device_names(device_ids, devicesdb_endpoint=devicesdb_endpoint)

    for record in records:
        device_id = record.get("DeviceId")
        record["device_name"] = names.get(device_id) if device_id else None

    return records
