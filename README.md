# Occupancy Calibration Dashboard

Basic internal dashboard for tracking `dozee.fsrcalibration` MDB records — device calibration status, pairing timeline, and full history audit trail.

## Features

- Summary counts for `learning`, `calibrated`, and `unpaired` devices
- Filter by status, organization ID, device ID, and latest status tag
- Search across device / user / org
- Expandable per-device history timeline (pairing, cron runs, manual calibration, unpair events)
- On-demand pickle inspection: EFS paths, per-sleep feature counts, merged unique epochs (360 minimum)

## Run locally

```bash
cd occupancy-dash
python3 server.py
```

Open [http://localhost:8765](http://localhost:8765).

Edit constants at the top of `server.py` and `pickle_inspector.py` to point at a different environment or EFS path.

The server serves static files and proxies MDB queries at `/api/records` to avoid browser CORS issues.

### Pickle inspection

Expand a device row, then click **Load Pickles**. The server queries recordsdb for sleep sessions since `paired_at`, resolves EFS pickle paths, and reads `sleep_occupancy_features` from each file on demand.

```
GET /api/pickles?device_id=...&user_id=...&paired_at=...
```

## Data source

Records come from:

```
GET {MDB_ENDPOINT}/api/dozee/fsrcalibration/query
```

See `fsr-threshold-estimator/docs/OCCUPANCY_CALIBRATION.md` for the full schema and status tag meanings.

## Related dashboards

| Dashboard | Folder | Collection |
| --------- | ------ | ---------- |
| CPT 99091 Notes | `../cpt-dash` | `dozee.notes` |

## Related APIs

| Service | Endpoint                             | Use                                              |
| ------- | ------------------------------------ | ------------------------------------------------ |
| devices | `GET /api/calibration/learning/list` | Devices in learning whose latest cron run failed |
| devices | `POST /api/calibration/run`          | Manual threshold application                     |
