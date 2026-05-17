# Nikon D3500 Local Controller

Small local FastAPI app that owns a Nikon D3500 over USB through `gphoto2`.
It provides a browser UI for live preview and still capture, and exposes JSON/file
endpoints that a Blender plugin can call later.

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

Open <http://127.0.0.1:8000>.

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

Capture formats:

- `jpeg`: default; stores a browser-friendly JPEG and removes any temporary NEF.
- `nef`: stores the camera download as-is.
- `nef+jpeg`: preserves the NEF and also generates a JPEG.

## API

- `GET /status`
- `POST /detect`
- `POST /preview/start`
- `POST /preview/stop`
- `GET /live.mjpg`
- `POST /capture`
- `POST /recover`
- `GET /camera/config`
- `PUT /camera/config/{key}`
- `POST /focus/autofocus`
- `POST /focus/manual-step`
- `GET /captures/latest`
- `GET /captures/{id}/file`

The controller kills macOS `PTPCamera` before camera operations and serializes all
`gphoto2` access so live preview and still capture do not fight for the USB device.
Still capture uses `--trigger-capture --wait-event-and-download=15s`; this proved
more reliable with the D3500 than `--capture-image-and-download`.

The settings API exposes mapped, tested camera controls for aperture, shutter
speed, ISO, exposure compensation, metering, white balance, image size, image
quality, capture mode, focus mode, and live-view autofocus mode. Settings and
focus actions pause live preview, run one `gphoto2` command, and resume preview
when it was previously running.
