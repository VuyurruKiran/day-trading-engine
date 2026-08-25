from __future__ import annotations

import argparse
import json
import math
import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from day_trading_engine.core.paths import project_root
from day_trading_engine.market_data.collector import build_default_collector
from day_trading_engine.providers.questrade import QuestradeError


@dataclass(frozen=True)
class CapacityReport:
    requested_symbols: int
    stored_quotes: int
    failed_symbols: tuple[str, ...]
    elapsed_seconds: float
    cpu_seconds: float
    peak_memory_mb: float | None
    valid_quotes: int
    max_latency_ms: int
    minimum_rest_requests: int
    equivalent_requests_per_hour: float
    stream_subscriptions: int
    reconnects: int
    passed: bool


def _peak_memory_mb() -> float | None:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                process, ctypes.byref(counters), counters.cb
            )
            return round(counters.PeakWorkingSetSize / (1024 * 1024), 2) if ok else None
        except (AttributeError, OSError):
            return None
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 * 1024 if platform.system() == "Darwin" else 1024
        return round(usage / divisor, 2)
    except (ImportError, OSError):
        return None


def run_capacity_gate(symbols: list[str], *, root: Path | None = None) -> CapacityReport:
    """Exercise the real collector against 30+ symbols and record provider/resource evidence."""
    normalized = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if len(normalized) < 30:
        raise ValueError("capacity gate requires at least 30 unique symbols")
    collector = build_default_collector(root or project_root())
    started = time.perf_counter()
    cpu_started = time.process_time()
    result = collector.collect(normalized)
    elapsed = max(time.perf_counter() - started, 1e-9)
    cpu_seconds = time.process_time() - cpu_started
    valid = [item for item in result.stored if item.is_trade_eligible]
    max_latency = max((item.latency_ms for item in result.stored), default=0)
    quote_batches = math.ceil(len(normalized) / collector.quote_batch_size)
    minimum_requests = len(normalized) + quote_batches
    return CapacityReport(
        requested_symbols=len(normalized),
        stored_quotes=len(result.stored),
        failed_symbols=result.failed_symbols,
        elapsed_seconds=round(elapsed, 3),
        cpu_seconds=round(cpu_seconds, 3),
        peak_memory_mb=_peak_memory_mb(),
        valid_quotes=len(valid),
        max_latency_ms=max_latency,
        minimum_rest_requests=minimum_requests,
        equivalent_requests_per_hour=round(minimum_requests / elapsed * 3600, 2),
        stream_subscriptions=0,
        reconnects=0,
        passed=len(valid) >= 30 and not result.failed_symbols,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the live 30-symbol Questrade capacity gate")
    parser.add_argument("symbols", nargs="+", help="At least 30 Questrade symbols")
    parser.add_argument("--output", type=Path, default=Path("data/capacity_gate.json"))
    args = parser.parse_args(argv)
    try:
        report = run_capacity_gate(args.symbols)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(asdict(report), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except (OSError, ValueError, QuestradeError) as exc:
        print(f"Questrade capacity gate failed: {exc}")
        return 2
    print(args.output)
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
