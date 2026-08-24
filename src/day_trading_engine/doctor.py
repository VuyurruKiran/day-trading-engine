from __future__ import annotations

import json

from day_trading_engine.core.health import run_health_check


def main() -> int:
    report, _ = run_health_check()
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
