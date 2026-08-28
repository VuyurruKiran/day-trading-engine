from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from day_trading_engine.core.health import run_health_check
from day_trading_engine.core.paths import ensure_runtime_dirs
from day_trading_engine.ui.state import ManualTrade, ReportStore


def _read_backup_status(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("backup status must be an object")
    return payload


def _local_datetime(day: date, clock: time, timezone: str) -> datetime:
    """Combine UI date/time input into one aware local timestamp."""
    return datetime.combine(day, clock, tzinfo=ZoneInfo(timezone))


def _manual_form_snapshot(
    latest_snapshot_id: str,
    latest_has_primary: bool,
    manual_history: tuple[ManualTrade, ...],
) -> str | None:
    """Prefer an open trade so its exit remains available across later decisions."""
    open_trade = next((trade for trade in manual_history if trade.exit_at is None), None)
    if open_trade is not None:
        return open_trade.snapshot_id
    return latest_snapshot_id if latest_has_primary else None


def _manual_trade_form(store: ReportStore, snapshot_id: str, timezone: str) -> None:
    """Render entry or exit input for the selected immutable decision snapshot."""
    try:
        trade = store.manual_trade(snapshot_id)
    except KeyError:
        trade = None

    now = datetime.now(ZoneInfo(timezone))
    if trade is None:
        with st.form("manual-entry"):
            st.subheader("Record Actual Entry")
            entry_day = st.date_input("Entry date", value=now.date())
            entry_time = st.time_input("Entry time", value=now.time().replace(microsecond=0))
            entry_price = st.number_input("Entry price", min_value=0.01, format="%.4f")
            quantity = st.number_input("Quantity", min_value=1, step=1)
            notes = st.text_area("Entry notes")
            submitted = st.form_submit_button("Save entry")
        if submitted:
            store.record_trade_entry(
                snapshot_id,
                at=_local_datetime(entry_day, entry_time, timezone),
                price=float(entry_price),
                quantity=int(quantity),
                notes=notes,
            )
            st.rerun()
        return

    if trade.exit_at is not None:
        st.info("This manual trade is closed.")
        return

    with st.form("manual-exit"):
        st.subheader(f"Record Actual Exit · {trade.symbol}")
        exit_day = st.date_input("Exit date", value=now.date())
        exit_time = st.time_input("Exit time", value=now.time().replace(microsecond=0))
        exit_price = st.number_input("Exit price", min_value=0.01, format="%.4f")
        reason = st.text_input("Exit reason")
        notes = st.text_area("Exit notes")
        submitted = st.form_submit_button("Save exit")
    if submitted:
        store.record_trade_exit(
            snapshot_id,
            at=_local_datetime(exit_day, exit_time, timezone),
            price=float(exit_price),
            reason=reason,
            notes=notes,
        )
        st.rerun()


def main() -> None:
    """Render system health, latest decision, and manual execution controls."""
    st.set_page_config(page_title="Day Trading Engine", layout="wide")
    st.title("Day Trading Research & Decision Engine")
    st.caption("Trading Engine V1 · Plan v2.2")

    report, config = run_health_check()
    st.subheader("System Health")
    st.success("Healthy") if report.ok else st.error("Degraded")
    st.json(report.to_dict())

    st.subheader("Locked V1 Contract")
    if config is None:
        st.warning("Configuration is invalid. Fix the reported config error before continuing.")
    else:
        cols = st.columns(4)
        cols[0].metric("Starting cash", f"${config.validation.starting_cash_usd:.0f}")
        cols[1].metric("Research candidates", config.research.daily_candidate_count)
        cols[2].metric("Max finalists", config.research.final_candidate_max)
        cols[3].metric("Max positions", config.validation.max_active_positions)

    try:
        data_dir, _ = ensure_runtime_dirs()
    except OSError:
        st.warning("Runtime directories are inaccessible.")
        return
    st.subheader("Research Data Protection")
    backup_status = data_dir / "backup_status.json"
    if not backup_status.exists():
        st.warning("No verified research-data backup has been recorded yet.")
    else:
        try:
            backup = _read_backup_status(backup_status)
            st.caption(f"Last backup: {backup['created_at']}")
            if backup.get("same_volume_as_source"):
                st.warning(
                    "Latest backup is on the same storage volume. It protects against accidental "
                    "deletion/corruption, not physical disk failure."
                )
            else:
                st.success("Latest backup is on a different storage volume.")
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            st.warning("Backup status is unreadable; run backup/restore verification again.")

    st.subheader("Latest Decision")
    try:
        state_path = data_dir / "decision_state.db"
        if not state_path.exists():
            st.info("No saved decision snapshot yet.")
            return
        store = ReportStore(state_path)
        latest = store.latest()
        if latest is None:
            st.info("No saved decision snapshot yet.")
            return
        transitions = store.transitions(latest.snapshot_id)
        executions = store.execution_events(latest.snapshot_id)
        manual_history = store.manual_trade_history()
    except (OSError, sqlite3.Error):
        st.info("Decision state is unavailable while local storage is inaccessible.")
        return

    st.caption(latest.created_at.isoformat())
    if latest.primary_symbol:
        st.success(f"PRIMARY: {latest.primary_symbol}")
    else:
        st.warning("NO TRADE / no primary candidate")
    st.json(latest.payload)

    timezone = "UTC" if config is None else config.project.timezone
    form_snapshot = _manual_form_snapshot(
        latest.snapshot_id, latest.primary_symbol is not None, manual_history
    )
    if form_snapshot is not None:
        try:
            _manual_trade_form(store, form_snapshot, timezone)
        except (KeyError, ValueError, sqlite3.Error) as exc:
            st.error(str(exc))

    if manual_history:
        st.subheader("Actual Trade History")
        st.dataframe(
            [
                {
                    "snapshot": trade.snapshot_id,
                    "symbol": trade.symbol,
                    "entry_time": trade.entry_at,
                    "entry_price": trade.entry_price,
                    "quantity": trade.quantity,
                    "exit_time": trade.exit_at,
                    "exit_price": trade.exit_price,
                    "exit_reason": trade.exit_reason,
                    "notes": trade.notes,
                }
                for trade in manual_history
            ],
            use_container_width=True,
            hide_index=True,
        )
    if transitions:
        st.subheader("Monitoring History")
        st.dataframe(
            [
                {"time": at, "status": status, "reason": reason}
                for at, status, reason in transitions
            ],
            use_container_width=True,
            hide_index=True,
        )
    if executions:
        st.subheader("Legacy Manual Execution History")
        st.dataframe(
            [
                {"kind": kind, "time": at, "price": price}
                for kind, at, price in executions
            ],
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
