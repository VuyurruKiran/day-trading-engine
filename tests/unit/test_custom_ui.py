import json
import shutil
import threading
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from day_trading_engine.engine.domain import DecisionStatus
from day_trading_engine.ui.server import _handler, _state_payload, _timestamp, _trade_route
from day_trading_engine.ui.state import ReportStore, SavedReport

ROOT = Path(__file__).resolve().parents[2]


def test_custom_ui_trade_routes_are_exact() -> None:
    assert _trade_route("/api/trades/snapshot-1/entry") == ("snapshot-1", "entry")
    assert _trade_route("/api/trades/snapshot-1/exit") == ("snapshot-1", "exit")
    assert _trade_route("/api/trades/snapshot-1/delete") is None


def test_custom_ui_localizes_wall_time_to_project_timezone() -> None:
    assert _timestamp("2026-08-27T16:00:00Z").tzinfo == UTC
    localized = _timestamp("2026-08-27T10:00:00", "America/Edmonton")
    assert localized.astimezone(UTC) == datetime(2026, 8, 27, 16, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone"):
        _timestamp("2026-08-27T10:00:00")


def test_custom_ui_contains_required_operator_controls() -> None:
    html = (ROOT / "src/day_trading_engine/ui/index.html").read_text(encoding="utf-8")
    for field in (
        "Plan Entry",
        "Stop",
        "Target",
        "Qty",
        "Entry",
        "Exit",
        "Exit reason",
        "Notes",
        "Monitoring History",
        "Planned vs Actual",
        "Data Protection",
        "Time (${data.timezone})",
    ):
        assert field in html
    assert "new Date(document.getElementById('at').value).toISOString()" not in html
    assert "const openTrade = data.trades.find(item => !item.exit_at)" in html
    assert "encodeURIComponent(tradeSnapshotId)" in html


def _ui_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "configs").mkdir(parents=True)
    shutil.copy(ROOT / "configs" / "v1.yaml", root / "configs" / "v1.yaml")
    data = root / "data"
    data.mkdir()
    (data / "backup_status.json").write_text(
        json.dumps(
            {
                "created_at": "2026-08-27T20:00:00+00:00",
                "destination": "/backup/example",
                "same_volume_as_source": True,
            }
        ),
        encoding="utf-8",
    )
    store = ReportStore(data / "decision_state.db")
    report = store.save_once(
        SavedReport(
            snapshot_id="2026-08-27-ui",
            created_at=datetime(2026, 8, 27, 16, 0, tzinfo=UTC),
            primary_symbol="AAPL",
            payload={
                "session": "2026-08-27",
                "decision_state": "PRIMARY",
                "primary": {
                    "symbol": "AAPL",
                    "entry": 100.0,
                    "stop": 98.0,
                    "target": 104.0,
                    "quantity": 1,
                    "expiry": "15:55 America/New_York",
                },
            },
        )
    )
    store.append_transition(
        report.snapshot_id,
        at=datetime(2026, 8, 27, 16, 5, tzinfo=UTC),
        status=DecisionStatus.HOLD,
        reason="monitoring",
    )
    return root


def _request(
    url: str,
    *,
    body: dict[str, object] | None = None,
    content_type: str = "application/json",
    origin: str | None = None,
) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode()
    headers: dict[str, str] = {}
    if data is not None:
        headers["Content-Type"] = content_type
    if origin is not None:
        headers["Origin"] = origin
    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def test_custom_ui_reports_unreadable_backup_without_failing_state(tmp_path: Path) -> None:
    root = _ui_root(tmp_path)
    (root / "data" / "backup_status.json").write_text("not-json", encoding="utf-8")

    assert _state_payload(root)["backup"] == {"status": "unreadable"}


def test_custom_ui_rejects_invalid_trade_posts(tmp_path: Path) -> None:
    root = _ui_root(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    url = base + "/api/trades/2026-08-27-ui/entry"
    payload = {"at": "2026-08-27T10:01", "price": 100.25, "quantity": 1}
    try:
        status, _ = _request(url, body=payload, content_type="text/plain")
        assert status == 415
        status, _ = _request(url, body=payload, origin="http://evil.example")
        assert status == 403
        for quantity in (1.5, "1e309"):
            status, body = _request(url, body={**payload, "quantity": quantity})
            assert status == 400
            assert b"positive whole number" in body
        assert ReportStore(root / "data" / "decision_state.db").manual_trade_history() == ()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_custom_ui_serves_state_and_keeps_open_trade_exit_accessible(tmp_path: Path) -> None:
    root = _ui_root(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, body = _request(base + "/")
        assert status == 200
        assert b"Day Trading Research" in body
        assert b"Plan Entry" in body

        status, body = _request(base + "/api/state")
        state = json.loads(body)
        assert status == 200
        assert state["timezone"] == "America/Edmonton"
        assert state["backup"]["status"] == "same_volume"
        assert state["latest"]["snapshot_id"] == "2026-08-27-ui"
        assert state["latest"]["payload"]["primary"]["target"] == 104.0
        assert state["transitions"] == [
            {
                "at": "2026-08-27T16:05:00+00:00",
                "status": "HOLD",
                "reason": "monitoring",
            }
        ]
        assert state["trades"] == []

        status, _ = _request(
            base + "/api/trades/2026-08-27-ui/entry",
            body={"at": "2026-08-27T10:01", "price": 100.25, "quantity": 1},
        )
        assert status == 200

        store = ReportStore(root / "data" / "decision_state.db")
        store.save_once(
            SavedReport(
                snapshot_id="2026-08-28-no-trade",
                created_at=datetime(2026, 8, 28, 16, 0, tzinfo=UTC),
                primary_symbol=None,
                payload={"session": "2026-08-28", "decision_state": "NO_TRADE"},
            )
        )
        _, body = _request(base + "/api/state")
        state = json.loads(body)
        assert state["latest"]["snapshot_id"] == "2026-08-28-no-trade"
        assert state["trades"][0]["exit_at"] is None

        status, _ = _request(
            base + "/api/trades/2026-08-27-ui/exit",
            body={
                "at": "2026-08-27T10:30",
                "price": 103.25,
                "reason": "target",
            },
        )
        assert status == 200

        _, body = _request(base + "/api/state")
        state = json.loads(body)
        trade = state["trades"][0]
        assert trade["entry_at"] == "2026-08-27T16:01:00+00:00"
        assert trade["exit_at"] == "2026-08-27T16:30:00+00:00"
        assert trade["exit_reason"] == "target"
        assert state["outcomes"][0]["realized_pnl"] == 3.0

        status, _ = _request(base + "/missing")
        assert status == 404
        status, body = _request(
            base + "/api/trades/2026-08-27-ui/entry",
            body={"at": "2026-08-27T10:01"},
        )
        assert status == 400
        assert b"price is required" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
