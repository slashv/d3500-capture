from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .camera_controller import CameraBusyError, CameraController, CameraError, CameraNotFoundError
from .capture_store import CaptureStore, media_type_for
from .settings import Settings


settings = Settings.from_env()
store = CaptureStore(settings.capture_dir)
controller = CameraController(settings, store)


class ConfigSetRequest(BaseModel):
    value: str


class ManualFocusRequest(BaseModel):
    value: int = Field(ge=-2000, le=2000)


class CaptureRequest(BaseModel):
    fast: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        controller.shutdown()


app = FastAPI(title="Nikon D3500 Local Controller", version="0.1.0", lifespan=lifespan)

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/status")
def status() -> dict[str, Any]:
    return controller.status()


@app.post("/detect")
def detect() -> dict[str, str | bool | None]:
    try:
        return controller.detect_camera()
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.post("/preview/start")
def start_preview() -> dict[str, Any]:
    try:
        return controller.start_preview()
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.post("/preview/stop")
def stop_preview() -> dict[str, Any]:
    try:
        return controller.stop_preview()
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.get("/live.mjpg")
def live_mjpg() -> StreamingResponse:
    return StreamingResponse(
        controller.mjpeg_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/capture")
def capture(request: CaptureRequest = Body(default_factory=CaptureRequest)) -> dict[str, Any]:
    try:
        return controller.capture(fast=request.fast)
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.post("/recover")
def recover() -> dict[str, Any]:
    return controller.recover()


@app.get("/camera/config")
def camera_config() -> dict[str, Any]:
    try:
        return controller.get_camera_config()
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.put("/camera/config/{key}")
def set_camera_config(key: str, request: ConfigSetRequest) -> dict[str, Any]:
    try:
        return controller.set_camera_config(key, request.value)
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.post("/focus/autofocus")
def autofocus() -> dict[str, Any]:
    try:
        return controller.autofocus()
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.post("/focus/manual-step")
def manual_focus_step(request: ManualFocusRequest) -> dict[str, Any]:
    try:
        return controller.manual_focus_step(request.value)
    except CameraError as exc:
        raise _http_error(exc) from exc


@app.get("/captures/latest")
def latest_capture() -> dict[str, Any] | None:
    return store.read_latest()


@app.get("/captures/{capture_id}/file")
def capture_file(capture_id: str) -> FileResponse:
    path = store.file_for_id(capture_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Capture {capture_id} was not found.")
    return FileResponse(path, media_type=media_type_for(path), filename=path.name)


def _http_error(exc: CameraError) -> HTTPException:
    if isinstance(exc, CameraBusyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, CameraNotFoundError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))
