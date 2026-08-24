from __future__ import annotations

import streamlit as st

from day_trading_engine.core.health import run_health_check


def main() -> None:
    st.set_page_config(page_title="Day Trading Engine", layout="wide")
    st.title("Day Trading Research & Decision Engine")
    st.caption("Software V1 foundation · Plan v2.2")

    report, config = run_health_check()
    st.subheader("System Health")
    if report.ok:
        st.success("Healthy")
    else:
        st.error("Degraded")
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

    st.info("Trading logic and Questrade integration are intentionally not part of M1.")


if __name__ == "__main__":
    main()
