# Nikon D3500 Local Controller

Local FastAPI app that owns a Nikon D3500 over USB through `gphoto2`. It provides
a browser UI for live preview and still capture, plus a small HTTP API that other
local software, such as a Blender plugin, can call.

The camera should have one owner. Let this service own USB/PTP access, then have
other apps talk to this service over HTTP or read returned local file paths.

## Requirements

- macOS
- Nikon D3500 connected over USB
- `gphoto2` at `/opt/homebrew/bin/gphoto2`
- `sips` at `/usr/bin/sips`
- `uv`

## Run

```sh
./scripts/start.sh
```

Open <http://127.0.0.1:8000>. FastAPI also exposes generated API docs at
<http://127.0.0.1:8000/docs> and OpenAPI JSON at
<http://127.0.0.1:8000/openapi.json>.

Equivalent direct command:

```sh
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Settings

Environment variables:

- `D3500_CAPTURE_DIR=./captures`
- `D3500_CAPTURE_FORMAT=jpeg`
- `D3500_CAMERA_MODEL="Nikon DSC D3500"`
- `D3500_GPHOTO2=/opt/homebrew/bin/gphoto2`
- `D3500_SIPS=/usr/bin/sips`
- `D3500_HOST=127.0.0.1`
- `D3500_PORT=8000`
- `D3500_GPHOTO_TIMEOUT=60`
- `D3500_CAPTURE_WAIT=5`
- `D3500_FAST_CAPTURE_WAIT=3`

Capture formats:

- `jpeg`: default; stores a browser-friendly JPEG and removes any temporary NEF.
- `nef`: stores the camera download as-is.
- `nef+jpeg`: preserves the NEF and also generates a JPEG.

## Integration Model

Use `file_path` when the caller runs on the same Mac, which is the expected
Blender plugin case. Use `file_url` only if the caller prefers HTTP download.

Recommended Blender-side flow:

1. `GET /status`
2. If `camera.detected` is false, call `POST /detect`
3. Optionally call `GET /camera/config` and set camera controls
4. Call `POST /capture` with `{ "fast": true }` for a prepared shooting session
5. Load the returned `file_path` into Blender

Do not run `gphoto2` from Blender while this service is running.

## API Contract

Base URL: `http://127.0.0.1:8000`

### `GET /status`

Returns current service state, camera detection, preview status, settings, and
latest capture metadata.

Example response:

```json
{
  "state": "previewing",
  "camera": {
    "model": "Nikon DSC D3500",
    "detected": true,
    "port": "usb:000,007"
  },
  "preview": {
    "running": true,
    "frame_id": 1832
  },
  "latest_capture": {
    "id": "20260517-073016",
    "created_at": "2026-05-17T10:01:53+02:00",
    "format": "jpeg",
    "fast": true,
    "file_path": "/absolute/path/to/captures/nikon-d3500-20260517-073016.JPG",
    "file_url": "/captures/20260517-073016/file",
    "jpeg_path": "/absolute/path/to/captures/nikon-d3500-20260517-073016.JPG"
  },
  "settings": {
    "capture_dir": "/absolute/path/to/captures",
    "capture_format": "jpeg",
    "capture_wait": 5,
    "fast_capture_wait": 3
  },
  "active_command": null,
  "error": null
}
```

State values:

- `idle`: camera is available and preview is stopped.
- `previewing`: MJPEG preview process is running.
- `capturing`: still capture is in progress.
- `busy`: another camera operation is in progress.
- `camera_not_found`: configured camera model was not detected.
- `error`: the last camera operation failed; inspect `error`.

### `POST /detect`

Kills macOS `PTPCamera`, runs `gphoto2 --auto-detect`, and updates the camera
port.

Example response:

```json
{
  "model": "Nikon DSC D3500",
  "detected": true,
  "port": "usb:000,007"
}
```

### `POST /preview/start`

Starts Nikon live view and the MJPEG preview subprocess. Returns the same shape
as `GET /status`.

### `POST /preview/stop`

Stops the preview subprocess and turns Nikon viewfinder/live view off. Returns
the same shape as `GET /status`.

### `GET /live.mjpg`

MJPEG preview stream for browsers or preview consumers. This is not the
high-resolution still image path. Still capture is always through `POST /capture`.

### `GET /preview/frame.jpg`

Returns the latest live preview JPEG frame with `Cache-Control: no-store` and
`X-D3500-Frame-Id`. This is intended for polling clients that should not keep a
long-lived MJPEG connection open.

### `POST /capture`

Triggers a still capture, downloads the file, writes `captures/latest.json`, and
returns capture metadata.

Request body is optional:

```json
{
  "fast": false
}
```

Normal capture (`fast: false`) is conservative:

- stops preview if needed
- kills `PTPCamera`
- re-detects the camera
- applies capture target and image quality
- triggers capture and downloads the file
- restarts preview if it was running

Fast capture (`fast: true`) assumes the camera is already detected and prepared:

- stops preview if needed
- skips repeated camera preparation
- uses `D3500_FAST_CAPTURE_WAIT`
- restarts preview if it was running

Example response:

```json
{
  "id": "20260517-073016",
  "created_at": "2026-05-17T10:01:53+02:00",
  "format": "jpeg",
  "fast": true,
  "file_path": "/absolute/path/to/captures/nikon-d3500-20260517-073016.JPG",
  "file_url": "/captures/20260517-073016/file",
  "jpeg_path": "/absolute/path/to/captures/nikon-d3500-20260517-073016.JPG"
}
```

If `D3500_CAPTURE_FORMAT=nef+jpeg`, the response can also include:

```json
{
  "raw_path": "/absolute/path/to/captures/nikon-d3500-20260517-073016.NEF"
}
```

### `POST /recover`

Terminates active `gphoto2` processes owned by this service, turns viewfinder off,
clears the active command, and returns `GET /status` shape. Use this if a camera
operation hangs.

### `GET /captures/latest`

Returns latest capture metadata, or `null` if no capture exists yet.

### `GET /captures/{id}/file`

Serves the primary file for a capture. For local Blender use, prefer the absolute
`file_path` from `/capture` or `/captures/latest`.

## Camera Settings API

### `GET /camera/config`

Returns mapped controls, current values, and choices. The service pauses preview,
queries the camera, then resumes preview if it was running.

Available keys:

- `aperture`
- `shutter_speed`
- `iso`
- `exposure_compensation`
- `metering`
- `white_balance`
- `image_size`
- `image_quality`
- `capture_mode`
- `focus_mode`
- `live_view_af_mode`
- `live_view_af_focus`

Example control shape:

```json
{
  "key": "aperture",
  "path": "/main/capturesettings/f-number",
  "group": "exposure",
  "label": "F-Number",
  "readonly": false,
  "type": "RADIO",
  "current": "f/8",
  "choices": [
    { "value": "6", "label": "f/7.1" },
    { "value": "7", "label": "f/8" },
    { "value": "8", "label": "f/9" }
  ]
}
```

### `PUT /camera/config/{key}`

Sets one mapped camera control. Use `value` from the `choices` array returned by
`GET /camera/config`, not the display label.

Example:

```sh
curl -X PUT http://127.0.0.1:8000/camera/config/aperture \
  -H 'Content-Type: application/json' \
  -d '{"value":"7"}'
```

Request body:

```json
{
  "value": "7"
}
```

Response:

```json
{
  "control": {
    "key": "aperture",
    "current": "f/8"
  },
  "status": {
    "state": "previewing"
  }
}
```

### `POST /focus/autofocus`

Triggers Nikon autofocus and returns `GET /status` shape.

### `POST /focus/manual-step`

Drives manual focus by a bounded relative step. Valid values are integers from
`-2000` to `2000`, excluding `0`. The UI uses small safe steps: `-200`, `-50`,
`50`, `200`.

Request body:

```json
{
  "value": 50
}
```

## Error Handling

The API returns JSON errors using FastAPI's standard `detail` field.

- `409`: camera operation already in progress.
- `503`: camera not detected.
- `500`: `gphoto2`, conversion, timeout, or unexpected camera failure.

Example:

```json
{
  "detail": "Camera operation already in progress."
}
```

Call `POST /recover` if `active_command` remains non-null or the UI appears stuck
after a camera operation.

## Blender Python Example

Minimal standard-library example:

```python
import json
import urllib.request

BASE_URL = "http://127.0.0.1:8000"


def request_json(path, method="GET", body=None):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


status = request_json("/status")
if not status["camera"]["detected"]:
    request_json("/detect", method="POST")

capture = request_json("/capture", method="POST", body={"fast": True})
image_path = capture["file_path"]

# In Blender, use image_path with bpy.data.images.load(image_path), or pass it
# into your existing plugin material/texture import path.
print(image_path)
```

## Operational Notes

- The USB camera is single-owner. Do not run parallel `gphoto2` commands.
- macOS may start `PTPCamera`; this service kills it before camera operations.
- Preview and settings/capture operations are serialized by one controller lock.
- Settings and capture may pause live preview and resume it afterward.
- Fast capture is faster but assumes the camera target/quality/settings are
  already correct.
