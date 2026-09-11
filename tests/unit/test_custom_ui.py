import json
import shutil
import threading
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pytest

import day_trading_engine.ui.server as ui_server
from day_trading_engine.engine.domain import DecisionStatus
from day_trading_engine.ui.server import (
    _backup_payload,
    _handler,
    _quantity,
    _same_origin,
    _state_payload,
    _timestamp,
    _trade_route,
)
from day_trading_engine.ui.state import ReportStore, SavedReport

ROOT = Path(__file__).resolve().parents[2]
_TEST_NOW = datetime(2026, 8, 27, 10, 0, tzinfo=ZoneInfo("America/New_York"))


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _TEST_NOW if tz is None else _TEST_NOW.astimezone(tz)


def _freeze_ui_clock(monkeypatch) -> None:
    monkeypatch.setattr(ui_server, "datetime", _FrozenDateTime)


def test_custom_ui_trade_routes_are_exact() -> None:
    assert _trade_route("/api/trades/snapshot-1/entry") == ("snapshot-1", "entry")
    assert _trade_route("/api/trades/snapshot-1/exit") == ("snapshot-1", "exit")
    assert _trade_route("/api/trades/snapshot-1/missed") == ("snapshot-1", "missed")
    assert _trade_route("/api/trades/snapshot-1/delete") is None


def test_custom_ui_localizes_wall_time_to_project_timezone() -> None:
    assert _timestamp("2026-08-27T16:00:00Z").tzinfo == UTC
    localized = _timestamp("2026-08-27T10:00:00", "America/Edmonton")
    assert localized.astimezone(UTC) == datetime(2026, 8, 27, 16, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone"):
        _timestamp("2026-08-27T10:00:00")


def test_custom_ui_main_owns_server_lifecycle(tmp_path: Path, monkeypatch) -> None:
    events: list[str] = []

    class Server:
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 8767)
            assert handler is not None

        def serve_forever(self):
            events.append("serve")
            raise KeyboardInterrupt

        def server_close(self):
            events.append("close")

    monkeypatch.setattr(ui_server, "ThreadingHTTPServer", Server)
    assert ui_server.main(
        ["--host", "127.0.0.1", "--port", "8767", "--root", str(tmp_path)]
    ) == 0
    assert events == ["serve", "close"]


def test_custom_ui_validation_helpers_and_empty_state(tmp_path: Path) -> None:
    assert _trade_route("/bad") is None
    assert _same_origin(None, None, 8767)
    assert not _same_origin("http://127.0.0.1:8767", None, 8767)
    assert not _same_origin("not a url", "127.0.0.1", 8767)
    assert not _same_origin("http://127.0.0.1:bad", "127.0.0.1:8767", 8767)
    assert _same_origin("http://127.0.0.1:8767", "127.0.0.1:8767", 8767)
    for value in (True, "bad", float("nan"), 1.5):
        with pytest.raises(ValueError):
            _quantity(value)
    assert _quantity("2") == 2
    assert _backup_payload(tmp_path / "missing.json") == {"status": "missing"}
    assert _state_payload(tmp_path)["latest"] is None
    with pytest.raises(ValueError, match="timestamp"):
        _timestamp(123)

    report = tmp_path / "data" / "research" / "2026" / "08" / "monthly_report.json"
    report.parent.mkdir(parents=True)
    report.write_text("[]", encoding="utf-8")
    assert _state_payload(tmp_path)["research"]["monthly_report"] == {"status": "invalid"}
    report.write_text("not-json", encoding="utf-8")
    assert _state_payload(tmp_path)["research"]["monthly_report"] == {"status": "unreadable"}


def test_custom_ui_main_rejects_non_local_or_invalid_port(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ui_server.main(["--host", "0.0.0.0", "--root", str(tmp_path)])
    with pytest.raises(SystemExit):
        ui_server.main(["--port", "0", "--root", str(tmp_path)])


def test_custom_ui_reports_versioned_universe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        ui_server,
        "load_universe_snapshot",
        lambda *args, **kwargs: SimpleNamespace(
            universe_id="u1",
            effective_from="2026-08-28",
            selector_version="universe-v1",
            checksum="abc",
            members=("AAPL",),
            symbols=("AAPL",),
        ),
    )
    assert _state_payload(tmp_path)["research"]["universe"]["universe_id"] == "u1"


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
        "Qualified Finalists",
        "Missed Entry / No Fill",
        "Pre-market Evidence / Coverage / Freshness",
        "Prior Post-market Coverage / Freshness",
        "Extended Gates",
        "Time (${data.timezone})",
    ):
        assert field in html
    assert "new Date(document.getElementById('at').value).toISOString()" not in html
    assert "const openTrade = data.trades.find(item => !item.exit_at)" in html
    assert "encodeURIComponent(tradeSnapshotId)" in html


def test_custom_ui_formats_plan_prices_to_three_decimals() -> None:
    html = (ROOT / "src/day_trading_engine/ui/index.html").read_text(encoding="utf-8")
    assert "return Number.isFinite(number) ? number.toFixed(3) : '—';" in html
    for field in ("plan-entry", "plan-stop", "plan-target"):
        assert f"document.getElementById('{field}').textContent = formatPrice" in html
    assert (
        "Entry ${formatPrice(plan.entry)} · Stop ${formatPrice(plan.stop)} · "
        "Target ${formatPrice(plan.target)}"
    ) in html


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
            created_at=_TEST_NOW.astimezone(UTC),
            primary_symbol="AAPL",
            payload={
                "session": _TEST_NOW.date().isoformat(),
                "decision_state": "PRIMARY",
                "primary": {
                    "symbol": "AAPL",
                    "entry": 100.0,
                    "stop": 98.0,
                    "target": 104.0,
                    "quantity": 1,
                    "expiry": "15:55 America/New_York",
                },
                "finalists": [
                    {
                        "symbol": "AAPL",
                        "entry": 100.0,
                        "stop": 98.0,
                        "target": 104.0,
                        "quantity": 1,
                    },
                    {
                        "symbol": "MSFT",
                        "entry": 200.0,
                        "stop": 196.0,
                        "target": 208.0,
                        "quantity": 1,
                    },
                ],
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


def test_custom_ui_reports_unreadable_backup_without_failing_state(
    monkeypatch, tmp_path: Path
) -> None:
    _freeze_ui_clock(monkeypatch)
    root = _ui_root(tmp_path)
    (root / "data" / "backup_status.json").write_text("not-json", encoding="utf-8")

    assert _state_payload(root)["backup"] == {"status": "unreadable"}


def test_custom_ui_rejects_invalid_trade_posts(monkeypatch, tmp_path: Path) -> None:
    _freeze_ui_clock(monkeypatch)
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
        assert _request(base + "/unknown")[0] == 404
        assert _request(base + "/unknown", body=payload)[0] == 404
        assert _request(url, body=[])[0] == 400  # type: ignore[arg-type]
        assert _request(base + "/api/trades/missing/entry", body=payload)[0] == 404
        empty_post = Request(
            url, data=b"", headers={"Content-Type": "application/json"}, method="POST"
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(empty_post, timeout=5)  # noqa: S310
        assert raised.value.code == 400
        monkeypatch.setattr(
            ui_server,
            "_state_payload",
            lambda _: (_ for _ in ()).throw(ValueError("bad")),
        )
        assert _request(base + "/api/state")[0] == 500
        for quantity in (1.5, "1e309"):
            status, body = _request(url, body={**payload, "quantity": quantity})
            assert status == 400
            assert b"positive whole number" in body
        assert ReportStore(root / "data" / "decision_state.db").manual_trade_history() == ()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_custom_ui_records_missed_entry_without_creating_trade(
    monkeypatch, tmp_path: Path
) -> None:
    _freeze_ui_clock(monkeypatch)
    root = _ui_root(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, _ = _request(
            base + "/api/trades/2026-08-27-ui/missed",
            body={"at": "2026-08-27T10:01", "notes": "price moved away"},
        )
        assert status == 200
        _, body = _request(base + "/api/state")
        state = json.loads(body)
        assert state["trades"] == []
        assert state["outcomes"] == []
        assert state["dispositions"] == [
            {
                "snapshot_id": "2026-08-27-ui",
                "symbol": "AAPL",
                "at": "2026-08-27T16:01:00+00:00",
                "status": "missed_entry",
                "notes": "price moved away",
            }
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_custom_ui_serves_state_and_keeps_open_trade_exit_accessible(
    monkeypatch, tmp_path: Path
) -> None:
    _freeze_ui_clock(monkeypatch)
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
        assert b"Decision date &amp; time" in body
        assert b"Do not enter a paper trade" in body
        assert b"Backup verified on separate storage" in body
        assert b"No action needed" in body

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
