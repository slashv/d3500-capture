from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .capture_store import CaptureStore
from .settings import Settings


CONFIG_CONTROLS = {
    "aperture": {
        "path": "/main/capturesettings/f-number",
        "group": "exposure",
    },
    "shutter_speed": {
        "path": "/main/capturesettings/shutterspeed2",
        "group": "exposure",
    },
    "iso": {
        "path": "/main/imgsettings/iso",
        "group": "exposure",
    },
    "exposure_compensation": {
        "path": "/main/capturesettings/exposurecompensation",
        "group": "exposure",
    },
    "metering": {
        "path": "/main/capturesettings/exposuremetermode",
        "group": "exposure",
    },
    "white_balance": {
        "path": "/main/imgsettings/whitebalance",
        "group": "image",
    },
    "image_size": {
        "path": "/main/imgsettings/imagesize",
        "group": "image",
    },
    "image_quality": {
        "path": "/main/capturesettings/imagequality",
        "group": "image",
    },
    "capture_mode": {
        "path": "/main/capturesettings/capturemode",
        "group": "image",
    },
    "focus_mode": {
        "path": "/main/capturesettings/focusmode2",
        "group": "focus",
    },
    "live_view_af_mode": {
        "path": "/main/capturesettings/liveviewafmode",
        "group": "focus",
    },
    "live_view_af_focus": {
        "path": "/main/capturesettings/liveviewaffocus",
        "group": "focus",
    },
}


class CameraError(RuntimeError):
    pass


class CameraBusyError(CameraError):
    pass


class CameraNotFoundError(CameraError):
    pass


class CameraController:
    def __init__(self, settings: Settings, store: CaptureStore) -> None:
        self.settings = settings
        self.store = store
        self._operation_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._frame_condition = threading.Condition()
        self._preview_process: subprocess.Popen[bytes] | None = None
        self._preview_thread: threading.Thread | None = None
        self._active_process: subprocess.Popen[str] | None = None
        self._active_command: list[str] | None = None
        self._preview_stopping = False
        self._latest_frame: bytes | None = None
        self._frame_id = 0
        self._state = "idle"
        self._last_error: str | None = None
        self._camera_port: str | None = None
        self._camera_detected = False

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            preview_running = self._is_preview_running_locked()
            state = self._state
            if state == "previewing" and not preview_running:
                state = "idle"
            return {
                "state": state,
                "camera": {
                    "model": self.settings.camera_model,
                    "detected": self._camera_detected,
                    "port": self._camera_port,
                },
                "preview": {
                    "running": preview_running,
                    "frame_id": self._frame_id,
                },
                "latest_capture": self.store.read_latest(),
                "settings": self.settings.as_dict(),
                "active_command": self._active_command,
                "error": self._last_error,
            }

    def detect_camera(self) -> dict[str, str | bool | None]:
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")
        try:
            self._kill_ptp_camera()
            port = self._detect_camera_port()
            with self._state_lock:
                self._camera_detected = port is not None
                self._camera_port = port
                self._state = "idle" if port else "camera_not_found"
            return {
                "model": self.settings.camera_model,
                "detected": port is not None,
                "port": port,
            }
        finally:
            self._operation_lock.release()

    def start_preview(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")
        try:
            self._start_preview_locked()
            return self.status()
        finally:
            self._operation_lock.release()

    def stop_preview(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")
        try:
            self._stop_preview_locked(turn_off_viewfinder=True)
            return self.status()
        finally:
            self._operation_lock.release()

    def capture(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")

        resume_preview = False
        try:
            with self._state_lock:
                resume_preview = self._is_preview_running_locked()
                self._state = "capturing"
                self._last_error = None

            if resume_preview:
                self._stop_preview_locked(turn_off_viewfinder=True)
                with self._state_lock:
                    self._state = "capturing"
                time.sleep(0.75)

            self._kill_ptp_camera()
            self._ensure_camera_detected()
            self._prepare_camera_for_capture()

            before = self._capture_files()
            self._run_gphoto(
                [
                    "--filename",
                    str(self.settings.capture_dir / "nikon-d3500-%Y%m%d-%H%M%S.%C"),
                    "--trigger-capture",
                    f"--wait-event-and-download={self.settings.capture_wait}s",
                ],
                timeout=self.settings.gphoto_timeout,
            )
            downloaded = self._new_capture_files(before)
            if not downloaded:
                raise CameraError("Capture completed but no downloaded file was found.")

            metadata = self._prepare_capture_metadata(downloaded)
            self.store.write_latest(metadata)

            with self._state_lock:
                self._state = "idle"
                self._last_error = None

            if resume_preview:
                self._start_preview_locked()

            return metadata
        except Exception as exc:
            with self._state_lock:
                self._state = "error"
                self._last_error = str(exc)
            if resume_preview:
                try:
                    self._start_preview_locked()
                except Exception:
                    pass
            if isinstance(exc, CameraError):
                raise
            raise CameraError(str(exc)) from exc
        finally:
            self._operation_lock.release()

    def recover(self) -> dict[str, Any]:
        with self._state_lock:
            active_process = self._active_process
            preview_process = self._preview_process

        for process in (active_process, preview_process):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        try:
            self._run_gphoto(
                ["--set-config", "/main/actions/viewfinder=0"],
                timeout=10,
                check=False,
                track_active=False,
            )
        except Exception:
            pass

        with self._state_lock:
            self._active_process = None
            self._active_command = None
            self._preview_process = None
            self._preview_stopping = False
            self._state = "idle"
            self._last_error = None

        with self._frame_condition:
            self._frame_condition.notify_all()

        return self.status()

    def get_camera_config(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")
        try:
            controls = self._with_preview_pause(
                "busy",
                lambda: {
                    key: self._get_config_control(key)
                    for key in CONFIG_CONTROLS
                },
            )
            return {
                "controls": controls,
                "status": self.status(),
            }
        finally:
            self._operation_lock.release()

    def set_camera_config(self, key: str, value: str) -> dict[str, Any]:
        if key not in CONFIG_CONTROLS:
            raise CameraError(f"Unsupported camera config key: {key}")
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")
        try:
            control = self._with_preview_pause(
                "busy",
                lambda: self._set_config_control(key, value),
            )
            return {
                "control": control,
                "status": self.status(),
            }
        finally:
            self._operation_lock.release()

    def autofocus(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")
        try:
            self._with_preview_pause(
                "busy",
                lambda: self._run_gphoto(
                    ["--set-config", "/main/actions/autofocusdrive=1"],
                    timeout=20,
                ),
            )
            return self.status()
        finally:
            self._operation_lock.release()

    def manual_focus_step(self, value: int) -> dict[str, Any]:
        if value < -2000 or value > 2000 or value == 0:
            raise CameraError("Manual focus step must be between -2000 and 2000, excluding 0.")
        if not self._operation_lock.acquire(blocking=False):
            raise CameraBusyError("Camera operation already in progress.")
        try:
            self._with_preview_pause(
                "busy",
                lambda: self._run_gphoto(
                    ["--set-config", f"/main/actions/manualfocusdrive={value}"],
                    timeout=20,
                ),
            )
            return self.status()
        finally:
            self._operation_lock.release()

    def mjpeg_frames(self) -> Iterator[bytes]:
        last_seen = -1
        while True:
            with self._frame_condition:
                self._frame_condition.wait_for(
                    lambda: self._frame_id != last_seen
                    or not self._is_preview_running(),
                    timeout=5,
                )
                if self._frame_id == last_seen and not self._is_preview_running():
                    break
                frame = self._latest_frame
                last_seen = self._frame_id

            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    + frame
                    + b"\r\n"
                )

    def shutdown(self) -> None:
        if self._operation_lock.acquire(blocking=False):
            try:
                self._stop_preview_locked(turn_off_viewfinder=True)
            finally:
                self._operation_lock.release()

    def _start_preview_locked(self) -> None:
        with self._state_lock:
            if self._is_preview_running_locked():
                self._state = "previewing"
                return
            self._state = "busy"
            self._last_error = None

        self._kill_ptp_camera()
        self._ensure_camera_detected()
        self._run_gphoto(["--set-config", "/main/actions/viewfinder=1"], timeout=20)

        command = [
            self.settings.gphoto2,
            *self._port_args(),
            "--stdout",
            "--capture-movie",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        with self._state_lock:
            self._preview_process = process
            self._preview_stopping = False
            self._state = "previewing"
            self._latest_frame = None
            self._frame_id = 0

        thread = threading.Thread(
            target=self._read_preview_frames,
            args=(process,),
            name="d3500-preview-reader",
            daemon=True,
        )
        self._preview_thread = thread
        thread.start()

    def _with_preview_pause(self, state: str, operation):
        with self._state_lock:
            resume_preview = self._is_preview_running_locked()
            self._state = state
            self._last_error = None

        if resume_preview:
            self._stop_preview_locked(turn_off_viewfinder=True)
            with self._state_lock:
                self._state = state
            time.sleep(0.5)

        try:
            self._kill_ptp_camera()
            self._ensure_camera_detected()
            result = operation()
            with self._state_lock:
                self._state = "idle"
                self._last_error = None
            if resume_preview:
                self._start_preview_locked()
            return result
        except Exception as exc:
            with self._state_lock:
                self._state = "error"
                self._last_error = str(exc)
            if resume_preview:
                try:
                    self._start_preview_locked()
                except Exception:
                    pass
            if isinstance(exc, CameraError):
                raise
            raise CameraError(str(exc)) from exc

    def _stop_preview_locked(self, turn_off_viewfinder: bool) -> None:
        with self._state_lock:
            process = self._preview_process
            self._preview_stopping = True
            if process is not None:
                self._state = "busy"

        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        with self._state_lock:
            if self._preview_process is process:
                self._preview_process = None
            self._state = "idle"

        with self._frame_condition:
            self._frame_condition.notify_all()

        if turn_off_viewfinder:
            try:
                self._run_gphoto(
                    ["--set-config", "/main/actions/viewfinder=0"],
                    timeout=20,
                    check=False,
                )
            finally:
                with self._state_lock:
                    self._preview_stopping = False

    def _read_preview_frames(self, process: subprocess.Popen[bytes]) -> None:
        buffer = bytearray()
        unexpected_stop = False
        try:
            if process.stdout is None:
                raise CameraError("Preview subprocess has no stdout pipe.")

            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        del buffer[:-1]
                        break
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start > 0:
                            del buffer[:start]
                        break
                    frame = bytes(buffer[start : end + 2])
                    del buffer[: end + 2]
                    with self._frame_condition:
                        self._latest_frame = frame
                        self._frame_id += 1
                        self._frame_condition.notify_all()
        except Exception as exc:
            unexpected_stop = True
            with self._state_lock:
                self._last_error = str(exc)
        finally:
            if process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            with self._state_lock:
                if self._preview_process is process:
                    self._preview_process = None
                    if not self._preview_stopping:
                        unexpected_stop = True
                        self._state = "error"
                        self._last_error = self._last_error or "Preview stopped unexpectedly."
                elif not self._preview_stopping and unexpected_stop:
                    self._state = "error"
            with self._frame_condition:
                self._frame_condition.notify_all()

    def _ensure_camera_detected(self) -> None:
        port = self._detect_camera_port()
        if not port:
            with self._state_lock:
                self._camera_detected = False
                self._camera_port = None
                self._state = "camera_not_found"
            raise CameraNotFoundError(f"{self.settings.camera_model} was not detected.")
        with self._state_lock:
            self._camera_detected = True
            self._camera_port = port

    def _detect_camera_port(self) -> str | None:
        result = subprocess.run(
            [self.settings.gphoto2, "--auto-detect"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            raise CameraError(result.stderr.strip() or "gphoto2 --auto-detect failed.")

        for line in result.stdout.splitlines():
            if self.settings.camera_model in line:
                parts = line.split()
                if parts:
                    return parts[-1]
        return None

    def _run_gphoto(
        self,
        args: list[str],
        *,
        timeout: int,
        check: bool = True,
        track_active: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.settings.gphoto2, *self._port_args(), *args]
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if track_active:
            with self._state_lock:
                self._active_process = process
                self._active_command = command

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            raise CameraError(
                f"gphoto2 timed out after {timeout}s: {' '.join(command)}"
            ) from exc
        finally:
            if track_active:
                with self._state_lock:
                    if self._active_process is process:
                        self._active_process = None
                        self._active_command = None

        result = subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout,
            stderr,
        )
        if check and result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise CameraError(message or f"gphoto2 failed with exit code {result.returncode}.")
        return result

    def _get_config_control(self, key: str) -> dict[str, Any]:
        definition = CONFIG_CONTROLS[key]
        result = self._run_gphoto(
            ["--get-config", definition["path"]],
            timeout=20,
        )
        return _parse_config_output(key, definition, result.stdout)

    def _set_config_control(self, key: str, value: str) -> dict[str, Any]:
        current = self._get_config_control(key)
        self._validate_config_value(current, value)
        path = CONFIG_CONTROLS[key]["path"]
        self._run_gphoto(["--set-config", f"{path}={value}"], timeout=20)
        return self._get_config_control(key)

    @staticmethod
    def _validate_config_value(control: dict[str, Any], value: str) -> None:
        control_type = control["type"]
        if control_type == "RADIO":
            allowed = {choice["value"] for choice in control["choices"]}
            if value not in allowed:
                raise CameraError(
                    f"{control['key']} must be one of: {', '.join(sorted(allowed))}"
                )
        elif control_type == "TOGGLE" and value not in {"0", "1"}:
            raise CameraError(f"{control['key']} must be 0 or 1.")
        elif control_type == "RANGE":
            range_info = control.get("range") or {}
            try:
                numeric_value = int(value)
            except ValueError as exc:
                raise CameraError(f"{control['key']} must be an integer.") from exc
            bottom = int(range_info.get("bottom", numeric_value))
            top = int(range_info.get("top", numeric_value))
            if numeric_value < bottom or numeric_value > top:
                raise CameraError(f"{control['key']} must be between {bottom} and {top}.")

    def _port_args(self) -> list[str]:
        with self._state_lock:
            return ["--port", self._camera_port] if self._camera_port else []

    def _capture_files(self) -> set[Path]:
        self.settings.capture_dir.mkdir(parents=True, exist_ok=True)
        return {
            path
            for path in self.settings.capture_dir.iterdir()
            if path.is_file() and path.name != self.store.latest_path.name
        }

    def _new_capture_files(self, before: set[Path]) -> list[Path]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            after = self._capture_files()
            created = [path for path in after - before if path.exists()]
            if created:
                return sorted(created, key=lambda path: path.stat().st_mtime, reverse=True)
            time.sleep(0.1)
        return []

    def _prepare_capture_metadata(self, downloaded: list[Path]) -> dict[str, Any]:
        raw_candidates = [path for path in downloaded if path.suffix.lower() == ".nef"]
        jpeg_candidates = [
            path for path in downloaded if path.suffix.lower() in {".jpg", ".jpeg"}
        ]
        raw_path: Path | None = raw_candidates[0] if raw_candidates else None
        jpeg_path: Path | None = jpeg_candidates[0] if jpeg_candidates else None
        source = raw_path or jpeg_path or downloaded[0]
        capture_id = _capture_id(source)
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        capture_format = self.settings.capture_format

        if capture_format in {"jpeg", "nef+jpeg"} and jpeg_path is None:
            jpeg_path = source.with_suffix(".jpg")
            self._convert_to_jpeg(source, jpeg_path)

        if capture_format == "jpeg":
            file_path = jpeg_path or source
            for path in raw_candidates:
                if path != file_path and path.exists():
                    path.unlink()
            raw_path = None
        elif capture_format == "nef":
            file_path = raw_path or source
        else:
            file_path = jpeg_path or raw_path or source

        metadata: dict[str, Any] = {
            "id": capture_id,
            "created_at": created_at,
            "format": capture_format,
            "file_path": str(file_path.resolve()),
            "file_url": f"/captures/{capture_id}/file",
        }
        if raw_path:
            metadata["raw_path"] = str(raw_path.resolve())
        if jpeg_path:
            metadata["jpeg_path"] = str(jpeg_path.resolve())
        return metadata

    def _prepare_camera_for_capture(self) -> None:
        self._run_gphoto(
            ["--set-config", "/main/settings/capturetarget=1"],
            timeout=10,
            check=False,
        )
        quality_value = {
            "jpeg": "2",
            "nef": "3",
            "nef+jpeg": "4",
        }[self.settings.capture_format]
        self._run_gphoto(
            ["--set-config", f"/main/capturesettings/imagequality={quality_value}"],
            timeout=10,
            check=False,
        )

    def _convert_to_jpeg(self, source: Path, output: Path) -> None:
        result = subprocess.run(
            [self.settings.sips, "-s", "format", "jpeg", str(source), "--out", str(output)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise CameraError(message or "JPEG conversion with sips failed.")

    def _is_preview_running(self) -> bool:
        with self._state_lock:
            return self._is_preview_running_locked()

    def _is_preview_running_locked(self) -> bool:
        return self._preview_process is not None and self._preview_process.poll() is None

    @staticmethod
    def _kill_ptp_camera() -> None:
        subprocess.run(
            ["killall", "PTPCamera"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _capture_id(path: Path) -> str:
    stem = path.stem
    prefix = "nikon-d3500-"
    return stem[len(prefix) :] if stem.startswith(prefix) else stem


def _parse_config_output(
    key: str,
    definition: dict[str, str],
    output: str,
) -> dict[str, Any]:
    control: dict[str, Any] = {
        "key": key,
        "path": definition["path"],
        "group": definition["group"],
        "label": key.replace("_", " ").title(),
        "readonly": True,
        "type": "UNKNOWN",
        "current": None,
        "choices": [],
    }

    range_info: dict[str, int] = {}
    for line in output.splitlines():
        if line.startswith("Label: "):
            control["label"] = line.removeprefix("Label: ").strip()
        elif line.startswith("Readonly: "):
            control["readonly"] = line.removeprefix("Readonly: ").strip() == "1"
        elif line.startswith("Type: "):
            control["type"] = line.removeprefix("Type: ").strip()
        elif line.startswith("Current: "):
            control["current"] = line.removeprefix("Current: ").strip()
        elif line.startswith("Choice: "):
            choice = line.removeprefix("Choice: ").strip()
            value, _, label = choice.partition(" ")
            control["choices"].append(
                {
                    "value": value,
                    "label": label.strip() or value,
                }
            )
        elif line.startswith("Bottom: "):
            range_info["bottom"] = int(line.removeprefix("Bottom: ").strip())
        elif line.startswith("Top: "):
            range_info["top"] = int(line.removeprefix("Top: ").strip())
        elif line.startswith("Step: "):
            range_info["step"] = int(line.removeprefix("Step: ").strip())

    if range_info:
        control["range"] = range_info
    return control
