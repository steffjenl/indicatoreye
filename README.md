# IndicatorEye

IndicatorEye is a small FastAPI service that detects fixed indicator lights in a camera image. It is not tied to Raspberry Pi and can run anywhere Docker or Python is available.

## Endpoints

- `GET /config`
- `GET /status`
- `GET /status?lamp=orange`
- `GET /homey`
- `GET /homey?lamp=orange`
- `POST /analyze` with multipart file field `file`

## Configuration

The runtime configuration lives in `config/config.json`.

Before first run, copy the example file:

```bash
cp config/config.example.json config/config.json
```

`config/config.json` is intended for local machine settings and is ignored by Git.

- `snapshot_url`: image URL to analyze
- `timeout_seconds`: HTTP timeout for snapshot download
- `tls_verify`: set to `false` to allow self-signed certificates
- `lamps`: lamp positions and thresholds

## Run With Docker

```bash
cp config/config.example.json config/config.json
docker compose up -d --build
```

## Run Locally

Python 3.12 or 3.13 is recommended.

```bash
/opt/homebrew/bin/python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -r requirements.txt
cp config/config.example.json config/config.json
CONFIG_PATH="$PWD/config/config.json" uvicorn app.main:app --host 127.0.0.1 --port 8010
```

## Test With File Upload

```bash
curl -F "file=@image.png" http://localhost:8000/analyze
```

## Test With Snapshot URL

```bash
curl "http://localhost:8000/status"
```

## Filter A Single Lamp

Both `/status` and `/homey` accept an optional `lamp` query parameter.

```bash
curl "http://localhost:8000/status?lamp=orange"
curl "http://localhost:8000/homey?lamp=green"
```

## Homey Output

The `/homey` endpoint returns a compact payload with only the lamp states.

```bash
curl "http://localhost:8000/homey"
```

Example response:

```json
{
  "success": true,
  "checked_at": 1784471307,
  "devices": [
    {
      "name": "orange",
      "on": false
    },
    {
      "name": "green",
      "on": false
    }
  ]
}
```

## Status Output

Example response from `/status`:

```json
{
  "ok": true,
  "checked_at": 1784468760,
  "image": { "width": 640, "height": 360 },
  "lamps": [
    { "name": "orange", "state": "on", "on": true },
    { "name": "green", "state": "on", "on": true }
  ],
  "request": {
    "snapshot_url": "https://192.168.24.223/snap.jpeg",
    "timeout_seconds": 10,
    "tls_verify": false
  }
}
```

## Self-Signed HTTPS

`tls_verify` is set to `false` by default in the config so HTTPS snapshots with self-signed certificates work on a trusted LAN.

## Lamp Calibration

Edit `config/config.json` to change lamp positions and thresholds.

- `x` and `y` are pixel coordinates from the top-left corner
- `radius` is the sampled area around the lamp

Current defaults are based on the supplied 640x360 test image:

- orange lamp: `x=355`, `y=75`, `radius=8`
- green lamp: `x=383`, `y=74`, `radius=10`

Threshold defaults:

- orange: `min_brightness=120`, `min_saturation=110`, `min_color_score=0.40`
- green: `min_brightness=110`, `min_saturation=80`, `min_color_score=0.50`

If lighting conditions change, use `/analyze` with test images and tune the thresholds, especially `min_brightness`.

## Troubleshooting: Snapshot Request Timed Out

If you see this on macOS in Docker:

```json
{"detail":"Snapshot request timed out"}
```

the container usually cannot reach the LAN camera, even if the host machine can.

Quick checks:

```bash
curl -vk "https://192.168.24.223/snap.jpeg"
docker compose exec -T indicatoreye python - <<'PY'
import socket
s=socket.socket(); s.settimeout(5)
try:
  s.connect(('192.168.24.223', 443))
  print('reachable')
except Exception as e:
  print('unreachable', e)
finally:
  s.close()
PY
```

If the host works but the container does not:

1. Run the service directly on the host instead of Docker.
2. Or create a local host proxy and access it through `host.docker.internal`.
