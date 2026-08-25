from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.providers._json_http import get_json

JsonFetcher = Callable[..., dict]


def _source_time(accepted: str, filed: str, fallback: datetime) -> datetime:
    if accepted:
        try:
            value = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        except ValueError:
            pass
    if filed:
        try:
            return datetime.fromisoformat(filed).replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback


class SecFilingsProvider:
    name = "sec"

    def __init__(
        self,
        cik: str | int,
        *,
        user_agent: str,
        forms: tuple[str, ...] = (),
        fetch_json: JsonFetcher = get_json,
    ) -> None:
        digits = str(cik).strip().lstrip("0") or "0"
        if not digits.isdigit():
            raise ValueError("cik must contain digits only")
        if not user_agent.strip():
            raise ValueError("SEC user_agent is required")
        self._cik = int(digits)
        self._user_agent = user_agent.strip()
        self._forms = set(forms)
        self._fetch_json = fetch_json

    def fetch(self, received_at: datetime) -> list[ContextRecord]:
        payload = self._fetch_json(
            f"https://data.sec.gov/submissions/CIK{self._cik:010d}.json",
            headers={"User-Agent": self._user_agent},
        )
        recent = payload.get("filings", {}).get("recent", {})
        accession_numbers = recent.get("accessionNumber", [])
        tickers = tuple(str(ticker) for ticker in payload.get("tickers", []))
        records: list[ContextRecord] = []
        for index, accession in enumerate(accession_numbers):
            accession_text = str(accession or "").strip()
            if not accession_text:
                continue
            form = str(_at(recent, "form", index))
            if self._forms and form not in self._forms:
                continue
            primary_document = str(_at(recent, "primaryDocument", index))
            url = None
            if primary_document:
                accession_path = accession_text.replace("-", "")
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/{self._cik}/"
                    f"{accession_path}/{primary_document}"
                )
            records.append(
                ContextRecord(
                    kind="filing",
                    provider=self.name,
                    external_id=accession_text,
                    title=f"{form or 'SEC'} filing",
                    source_at=_source_time(
                        str(_at(recent, "acceptanceDateTime", index)),
                        str(_at(recent, "filingDate", index)),
                        received_at,
                    ),
                    received_at=received_at,
                    symbols=tickers,
                    url=url,
                    payload={
                        "accession_number": accession_text,
                        "form": form,
                        "filing_date": _at(recent, "filingDate", index),
                        "report_date": _at(recent, "reportDate", index),
                        "primary_document": primary_document,
                    },
                )
            )
        return records


def _at(mapping: dict, key: str, index: int) -> object:
    values = mapping.get(key, [])
    return values[index] if index < len(values) else ""
