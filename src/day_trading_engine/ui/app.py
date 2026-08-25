from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import streamlit as st

from day_trading_engine.core.health import run_health_check
from day_trading_engine.core.paths import ensure_runtime_dirs
from day_trading_engine.ui.state import ReportStore


def _read_backup_status(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("backup status must be an object")
    return payload


def main() -> None:
    """Render system health plus the latest immutable decision report."""
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
    except (OSError, sqlite3.Error):
        st.info("Decision state is unavailable while local storage is inaccessible.")
        return

    st.caption(latest.created_at.isoformat())
    if latest.primary_symbol:
        st.success(f"PRIMARY: {latest.primary_symbol}")
    else:
        st.warning("NO TRADE / no primary candidate")
    st.json(latest.payload)
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
        st.subheader("Manual Execution History")
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
