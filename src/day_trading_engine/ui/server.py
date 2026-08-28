from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from day_trading_engine.core.config import load_config
from day_trading_engine.core.health import run_health_check
from day_trading_engine.core.paths import ensure_runtime_dirs, project_root
from day_trading_engine.ui.state import ReportStore

_MAX_BODY_BYTES = 16_384
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_TRADING_TIMEZONE = ZoneInfo("America/New_York")


def _read_backup_status(path: Path) -> dict[str, object]:
    """Read the locally persisted backup status object."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("backup status must be an object")
    return payload


def _backup_payload(path: Path) -> dict[str, object]:
    """Return a UI-safe backup status without breaking the whole dashboard."""
    if not path.exists():
        return {"status": "missing"}
    try:
        payload = _read_backup_status(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "unreadable"}
    result = dict(payload)
    result["status"] = "same_volume" if payload.get("same_volume_as_source") else "verified"
    return result


def _timestamp(value: object, timezone: str | None = None) -> datetime:
    """Parse an ISO timestamp, localizing UI wall time to the project timezone."""
    if not isinstance(value, str):
        raise ValueError("timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if timezone is None:
            raise ValueError("timestamp must include a timezone offset")
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _state_payload(root: Path) -> dict[str, object]:
    """Build the custom UI state from existing local stores."""
    health, config = run_health_check(root / "configs" / "v1.yaml")
    data_dir, _ = ensure_runtime_dirs(root)
    state_path = data_dir / "decision_state.db"
    payload: dict[str, object] = {
        "health": health.to_dict(),
        "timezone": "UTC" if config is None else config.project.timezone,
        "backup": _backup_payload(data_dir / "backup_status.json"),
        "latest": None,
        "trades": [],
        "outcomes": [],
        "transitions": [],
    }
    if not state_path.exists():
        return payload

    store = ReportStore(state_path)
    latest = store.latest()
    if latest is not None:
        latest_payload = dict(latest.payload)
        stale = latest_payload.get("session") != datetime.now(_TRADING_TIMEZONE).date().isoformat()
        primary_symbol = latest.primary_symbol
        if stale:
            primary_symbol = None
            latest_payload.update(
                {
                    "decision": "NO TRADE",
                    "decision_state": "STALE",
                    "no_trade_reason": "latest saved decision is from a previous session",
                }
            )
        payload["latest"] = {
            "snapshot_id": latest.snapshot_id,
            "created_at": latest.created_at.isoformat(),
            "primary_symbol": primary_symbol,
            "payload": latest_payload,
            "stale": stale,
        }
        payload["transitions"] = [
            {"at": at, "status": status, "reason": reason}
            for at, status, reason in store.transitions(latest.snapshot_id)
        ]
    payload["trades"] = [asdict(item) for item in store.manual_trade_history()]
    payload["outcomes"] = [asdict(item) for item in store.trade_outcome_history()]
    return payload


def _trade_route(path: str) -> tuple[str, str] | None:
    """Parse the two supported manual-trade API routes."""
    parts = [unquote(part) for part in path.strip("/").split("/")]
    if len(parts) != 4 or parts[:2] != ["api", "trades"]:
        return None
    snapshot_id, action = parts[2], parts[3]
    if not snapshot_id or action not in {"entry", "exit"}:
        return None
    return snapshot_id, action


def _same_origin(origin: str | None, host: str | None, server_port: int) -> bool:
    """Allow non-browser clients or a browser request from this exact local origin."""
    if origin is None:
        return True
    if host is None:
        return False
    try:
        origin_parts = urlsplit(origin)
        host_parts = urlsplit(f"http://{host}")
        origin_port = origin_parts.port or 80
        host_port = host_parts.port or 80
    except ValueError:
        return False
    return (
        origin_parts.scheme == "http"
        and origin_parts.hostname in _LOCAL_HOSTS
        and origin_parts.hostname == host_parts.hostname
        and origin_port == host_port == server_port
    )


def _required(body: dict[str, Any], key: str) -> object:
    try:
        return body[key]
    except KeyError as exc:
        raise ValueError(f"{key} is required") from exc


def _quantity(value: object) -> int:
    """Parse a finite positive whole-number share quantity without truncation."""
    if isinstance(value, bool):
        raise ValueError("quantity must be a positive whole number")
    try:
        quantity = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("quantity must be a positive whole number") from exc
    if not isfinite(quantity) or quantity < 1 or not quantity.is_integer():
        raise ValueError("quantity must be a positive whole number")
    return int(quantity)


def _apply_trade(root: Path, snapshot_id: str, action: str, body: dict[str, Any]) -> None:
    """Apply one entry/exit request through the existing ReportStore contract."""
    data_dir, _ = ensure_runtime_dirs(root)
    store = ReportStore(data_dir / "decision_state.db")
    timezone = load_config(root / "configs" / "v1.yaml").project.timezone
    at = _timestamp(body.get("at"), timezone)
    if action == "entry":
        report = store.load(snapshot_id)
        if report.payload.get("session") != datetime.now(_TRADING_TIMEZONE).date().isoformat():
            raise ValueError("manual entry requires a current-session PRIMARY decision")
        store.record_trade_entry(
            snapshot_id,
            at=at,
            price=float(_required(body, "price")),
            quantity=_quantity(_required(body, "quantity")),
            notes=str(body.get("notes", "")),
        )
        return
    store.record_trade_exit(
        snapshot_id,
        at=at,
        price=float(_required(body, "price")),
        reason=str(_required(body, "reason")),
        notes=str(body.get("notes", "")),
    )


def _handler(root: Path) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one project root."""
    index = Path(__file__).with_name("index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: HTTPStatus, payload: object) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(index)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(index)
                return
            if path == "/api/state":
                try:
                    self._json(HTTPStatus.OK, _state_payload(root))
                except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            route = _trade_route(urlsplit(self.path).path)
            if route is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if self.headers.get_content_type() != "application/json":
                self._json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": "application/json required"},
                )
                return
            if not _same_origin(
                self.headers.get("Origin"), self.headers.get("Host"), self.server.server_port
            ):
                self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request rejected"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > _MAX_BODY_BYTES:
                    raise ValueError("invalid request body size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("request body must be an object")
                _apply_trade(root, route[0], route[1], body)
            except KeyError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"ok": True})

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    """Run the local-only custom web UI."""
    parser = argparse.ArgumentParser(description="Run the local day-trading web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args(argv)
    if args.host not in _LOCAL_HOSTS:
        parser.error("--host must be 127.0.0.1 or localhost for the local-only UI")
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be between 1 and 65535")
    server = ThreadingHTTPServer((args.host, args.port), _handler(args.root))
    print(f"Day Trading Engine UI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
