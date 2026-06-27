"""Inspect occupancy feature pickles on EFS for a device/user pair."""

from __future__ import annotations

import json
import os
import pickle
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

MIN_FEATURE_EPOCHS = 360
DEFAULT_EFS_DIR = "~/Desktop/efs/dozee/picklefiles-sit"
DEFAULT_RECORDSDB = "http://recordsdb-sit.dozee.int"


def parse_date(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        text = val
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            main_part = text
            tz_part = ""
            if "+" in text:
                main_part, tz_part = text.split("+", 1)
                tz_part = "+" + tz_part
            elif "-" in text[10:]:
                idx = text.find("-", 10)
                main_part = text[:idx]
                tz_part = text[idx:]

            if "." in main_part:
                time_part, frac_part = main_part.split(".", 1)
                frac_part = (frac_part + "000000")[:6]
                main_part = time_part + "." + frac_part

            try:
                return datetime.fromisoformat(main_part + tz_part)
            except ValueError:
                pass

            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(val, fmt)
                except ValueError:
                    continue
            raise
    return None


def get_efs_root(override: Optional[str] = None) -> str:
    return os.path.expanduser(override or DEFAULT_EFS_DIR)


def _resolve_pickle_path(
    *,
    efs_root: str,
    user_id: str,
    device_id: str,
    sleep_id: str,
    pkl_start: Any,
) -> tuple[Optional[str], str, list[str]]:
    checked_paths: list[str] = []
    try:
        efs_path = build_efs_pickle_path(efs_root, user_id, device_id, sleep_id, pkl_start)
        checked_paths.append(efs_path)
        if os.path.exists(efs_path):
            return efs_path, "efs", checked_paths
    except ValueError:
        pass

    return checked_paths[0] if checked_paths else None, "missing", checked_paths


def _http_get_json(url: str, params: dict[str, list[str]], timeout: int = 15) -> Any:
    query = urllib.parse.urlencode(params, doseq=True)
    full_url = f"{url}?{query}" if query else url
    req = urllib.request.Request(full_url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_efs_pickle_path(
    efs_root: str,
    user_id: str,
    device_id: str,
    sleep_id: str,
    pkl_start: Any,
) -> str:
    pkl_start_dt = parse_date(pkl_start)
    if not pkl_start_dt:
        raise ValueError(f"Invalid PklStartTime: {pkl_start}")
    year_month = pkl_start_dt.strftime("%Y-%m")
    day = pkl_start_dt.day
    return os.path.join(
        efs_root,
        year_month,
        str(day),
        str(user_id),
        str(device_id),
        str(sleep_id),
        f"sleepdata_{sleep_id}.pkl",
    )


def _load_features_from_pickle(pkl_path: str) -> tuple[int, list[float]]:
    with open(pkl_path, "rb") as handle:
        sleep_data = pickle.load(handle)
    features = sleep_data.get("sleep_occupancy_features", [])
    if not isinstance(features, list):
        features = []
    timestamps: list[float] = []
    for item in features:
        ts = item.get("timestamp") if isinstance(item, dict) else None
        if isinstance(ts, (int, float)):
            timestamps.append(float(ts))
    return len(features), timestamps


def inspect_pickles(
    device_id: str,
    user_id: str,
    paired_at: str,
    *,
    efs_root: Optional[str] = None,
    recordsdb_endpoint: Optional[str] = None,
) -> dict[str, Any]:
    efs_root = get_efs_root(efs_root)
    efs_root_exists = os.path.isdir(efs_root)
    recordsdb_endpoint = recordsdb_endpoint or DEFAULT_RECORDSDB

    if not device_id or not user_id:
        return {"error": "device_id and user_id are required"}
    if not paired_at:
        return {"error": "paired_at is required to scope sleep sessions"}

    paired_dt = parse_date(paired_at)
    if not paired_dt:
        return {"error": f"Invalid paired_at: {paired_at}"}

    try:
        sleeps = _http_get_json(
            f"{recordsdb_endpoint.rstrip('/')}/sleeps",
            {
                "UserId": [f"eq.{user_id}"],
                "DeviceId": [f"eq.{device_id}"],
                "select": ["SleepId,BedTime,DeviceId,Properties"],
                "Status": ["not.eq.DISOWNED", "not.eq.INVALID"],
            },
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"error": f"recordsdb query failed ({exc.code}): {body or exc.reason}"}
    except Exception as exc:
        return {"error": f"recordsdb query failed: {exc}"}

    if not isinstance(sleeps, list):
        sleeps = []

    sessions: list[dict[str, Any]] = []
    unique_ts_seen: set[Any] = set()
    merged_features = 0
    pickles_found = 0
    pickles_missing = 0
    eligible_sleeps = 0
    timestamps: list[float] = []

    for sleep in sleeps:
        bed_time_str = sleep.get("BedTime")
        if not bed_time_str:
            continue

        sleep_device_id = sleep.get("DeviceId")
        if sleep_device_id and sleep_device_id != device_id:
            continue

        sleep_dt = datetime.fromisoformat(bed_time_str)
        if paired_dt.tzinfo:
            sleep_dt = sleep_dt.replace(tzinfo=timezone.utc)

        if sleep_dt.date() < paired_dt.date():
            continue

        sleep_id = sleep.get("SleepId")
        props = sleep.get("Properties") or {}
        pkl_start = props.get("PklStartTime")
        if not pkl_start or not sleep_id:
            continue

        eligible_sleeps += 1
        pkl_path, source, checked_paths = _resolve_pickle_path(
            efs_root=efs_root,
            user_id=user_id,
            device_id=device_id,
            sleep_id=sleep_id,
            pkl_start=pkl_start,
        )

        session: dict[str, Any] = {
            "sleep_id": sleep_id,
            "bed_time": bed_time_str,
            "pkl_path": pkl_path,
            "checked_paths": checked_paths,
            "source": source,
            "exists": source != "missing",
            "feature_count": 0,
            "error": None,
        }

        if session["exists"] and pkl_path:
            pickles_found += 1
            try:
                feature_count, feature_timestamps = _load_features_from_pickle(pkl_path)
                session["feature_count"] = feature_count
                for ts in feature_timestamps:
                    if ts not in unique_ts_seen:
                        unique_ts_seen.add(ts)
                        merged_features += 1
                        timestamps.append(ts)
            except Exception as exc:
                session["error"] = str(exc)
        else:
            pickles_missing += 1

        sessions.append(session)

    sessions.sort(key=lambda row: row.get("bed_time") or "", reverse=True)

    time_range = None
    if timestamps:
        start_t = datetime.fromtimestamp(min(timestamps), timezone.utc).isoformat()
        end_t = datetime.fromtimestamp(max(timestamps), timezone.utc).isoformat()
        time_range = {"start": start_t, "end": end_t}

    result: dict[str, Any] = {
        "device_id": device_id,
        "user_id": user_id,
        "paired_at": paired_at,
        "efs_root": efs_root,
        "efs_root_exists": efs_root_exists,
        "recordsdb_endpoint": recordsdb_endpoint,
        "total_sleeps_in_db": len(sleeps),
        "eligible_sleeps": eligible_sleeps,
        "pickles_found": pickles_found,
        "pickles_missing": pickles_missing,
        "merged_unique_features": merged_features,
        "minimum_required": MIN_FEATURE_EPOCHS,
        "meets_minimum": merged_features >= MIN_FEATURE_EPOCHS,
        "time_range": time_range,
        "sessions": sessions,
    }

    if not efs_root_exists:
        result["warning"] = (
            f"EFS root not found at {efs_root}. "
            "Update DEFAULT_EFS_DIR in pickle_inspector.py or use the sidebar path override."
        )
    elif eligible_sleeps == 0 and len(sleeps) == 0:
        result["warning"] = (
            "No sleep sessions in recordsdb for this user/device yet. "
            "Pickle features appear after compute processes uploaded sensor files "
            "(typically after the first sleep session post-pairing)."
        )
    elif eligible_sleeps == 0 and len(sleeps) > 0:
        result["warning"] = (
            f"Found {len(sleeps)} sleep session(s) in recordsdb, but none on/after pairing date "
            f"({paired_at[:10]}) for this device. Older sleeps from a prior pairing are excluded."
        )
    elif eligible_sleeps > 0 and pickles_found == 0:
        result["warning"] = (
            f"Found {eligible_sleeps} eligible sleep(s) since pairing, but no pickle files on disk. "
            f"Checked EFS root: {efs_root}"
        )

    return result
