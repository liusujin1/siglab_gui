from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(os.environ.get("PYTHONVNA_PUBLISH_ROOT", "/data")).resolve()
TOKEN = os.environ.get("PYTHONVNA_PUBLISH_TOKEN", "")
MAX_BYTES = int(os.environ.get("PYTHONVNA_MAX_UPLOAD_BYTES", str(300 * 1024 * 1024)))
KEEP_FULL_RELEASES = max(1, int(os.environ.get("PYTHONVNA_KEEP_FULL_RELEASES", "2")))

FULL_PATTERN = re.compile(r"^PythonVNA_Suite_v(.+)\.(?:7z|zip)$", re.IGNORECASE)
UPDATE_PATTERN = re.compile(r"^PythonVNA_Update_v.+_to_v.+\.(?:7z|zip)$", re.IGNORECASE)
ALLOWED_NAMES = re.compile(
    r"^(?:manifest\.json|update_config\.json|"
    r"PythonVNA_Suite_v[^/\\]+\.(?:7z|zip)|"
    r"PythonVNA_Update_v[^/\\]+_to_v[^/\\]+\.(?:7z|zip))$",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_name(item: object) -> str:
    if not isinstance(item, dict):
        raise ValueError("manifest archive entry must be an object")
    url = str(item.get("url", "")).strip()
    name = Path(unquote(urlparse(url).path)).name
    if not name or not ALLOWED_NAMES.fullmatch(name):
        raise ValueError(f"manifest contains an invalid archive URL: {url!r}")
    return name


def validate_archive_entry(item: object) -> str:
    name = archive_name(item)
    path = ROOT / name
    if not path.is_file():
        raise ValueError(f"manifest references a missing archive: {name}")
    expected_size = int(item.get("size", -1))
    if path.stat().st_size != expected_size:
        raise ValueError(f"manifest size does not match archive: {name}")
    expected_hash = str(item.get("sha256", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError(f"manifest SHA256 is invalid: {name}")
    if sha256_file(path) != expected_hash:
        raise ValueError(f"manifest SHA256 does not match archive: {name}")
    return name


def validate_manifest(path: Path) -> set[str]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not str(manifest.get("latest", "")).strip():
        raise ValueError("manifest latest version is missing")
    keep = {"manifest.json", "update_config.json", validate_archive_entry(manifest.get("full"))}
    updates = manifest.get("updates", [])
    if not isinstance(updates, list):
        raise ValueError("manifest updates must be an array")
    for item in updates:
        keep.add(validate_archive_entry(item))
    return keep


def version_key(value: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", value))
    return parts or (0,)


def prune_archives(referenced: set[str]) -> tuple[list[str], int]:
    full_archives: list[tuple[tuple[int, ...], str]] = []
    candidates: list[Path] = []
    for path in ROOT.iterdir():
        if not path.is_file():
            continue
        match = FULL_PATTERN.fullmatch(path.name)
        if match:
            full_archives.append((version_key(match.group(1)), path.name))
            candidates.append(path)
        elif UPDATE_PATTERN.fullmatch(path.name):
            candidates.append(path)
    full_archives.sort(key=lambda item: item[0], reverse=True)
    keep = set(referenced)
    keep.update(name for _version, name in full_archives[:KEEP_FULL_RELEASES])

    removed: list[str] = []
    freed = 0
    for path in candidates:
        if path.name in keep:
            continue
        size = path.stat().st_size
        path.unlink()
        removed.append(path.name)
        freed += size
    return sorted(removed), freed


class PublishHandler(BaseHTTPRequestHandler):
    server_version = "PythonVNA-Publisher/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {fmt % args}", flush=True)

    def send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {TOKEN}"
        if TOKEN and hmac.compare_digest(supplied, expected):
            return True
        self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return False

    def do_GET(self) -> None:
        if not self.authorized():
            return
        if self.path.rstrip("/") == "/pythonvna-admin/health":
            self.send_json(HTTPStatus.OK, {"ok": True, "service": "pythonvna-publisher"})
            return
        prefix = "/pythonvna-admin/files/"
        if self.path.startswith(prefix):
            name = unquote(self.path[len(prefix) :])
            if not name or Path(name).name != name or not ALLOWED_NAMES.fullmatch(name):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid filename"})
                return
            path = ROOT / name
            if not path.is_file():
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "file not found"})
                return
            self.send_json(
                HTTPStatus.OK,
                {"ok": True, "name": name, "size": path.stat().st_size, "sha256": sha256_file(path)},
            )
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_PUT(self) -> None:
        prefix = "/pythonvna-admin/files/"
        if not self.path.startswith(prefix):
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        if not self.authorized():
            return

        name = unquote(self.path[len(prefix) :])
        if not name or Path(name).name != name or not ALLOWED_NAMES.fullmatch(name):
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid filename"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_BYTES:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "invalid upload size"})
            return
        expected_hash = self.headers.get("X-SHA256", "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing or invalid X-SHA256"})
            return

        ROOT.mkdir(parents=True, exist_ok=True)
        temporary = ROOT / f".{name}.{uuid.uuid4().hex}.upload"
        digest = hashlib.sha256()
        remaining = content_length
        started = time.monotonic()
        try:
            with temporary.open("xb") as handle:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("upload ended before Content-Length bytes were received")
                    handle.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            actual_hash = digest.hexdigest()
            if actual_hash != expected_hash:
                raise ValueError("uploaded SHA256 does not match X-SHA256")

            referenced: set[str] | None = None
            if name == "manifest.json":
                referenced = validate_manifest(temporary)
            os.replace(temporary, ROOT / name)
            os.chmod(ROOT / name, 0o644)

            removed: list[str] = []
            freed = 0
            if referenced is not None:
                removed, freed = prune_archives(referenced)
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "name": name,
                    "size": content_length,
                    "sha256": actual_hash,
                    "seconds": round(time.monotonic() - started, 3),
                    "removed": removed,
                    "freed_bytes": freed,
                },
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            temporary.unlink(missing_ok=True)
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("PYTHONVNA_PUBLISH_TOKEN is required")
    ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8080), PublishHandler)
    print(f"PythonVNA publisher listening on :8080, root={ROOT}", flush=True)
    server.serve_forever()
