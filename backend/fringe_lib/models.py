from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PerformanceRow:
    show_title: str
    slug: str
    genre: str
    venue: str
    performance_id: int
    performance_title: str
    date_local: str
    time_local: str
    datetime_utc: str
    ticket_status: str
    sold_out_flag: bool
    box_office_id: str | None
    percent_remaining: int | None = None
    availability_level: str | None = None
    availability: str = ""
    url: str = ""
    price_types: list[str] | None = None
    offers: list[dict[str, str]] | None = None

    def _offer_labels(self) -> list[str]:
        return [
            (o.get("label") or o.get("code") or "").strip()
            for o in (self.offers or [])
            if (o.get("label") or o.get("code") or "").strip()
        ]

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability,
            "show_title": self.show_title,
            "genre": self.genre,
            "venue": self.venue,
            "date": self.date_local,
            "time": self.time_local,
            "performance_title": self.performance_title,
            "ticket_status": self.ticket_status,
            "sold_out": self.availability == "sold_out",
            "percent_remaining": self.percent_remaining
            if self.percent_remaining is not None
            else "",
            "availability_level": self.availability_level or "",
            "offers": "; ".join(self._offer_labels()),
            "url": self.url,
            "performance_id": self.performance_id,
            "box_office_id": self.box_office_id or "",
            "datetime_utc": self.datetime_utc,
        }

    def to_public_dict(self) -> dict[str, Any]:
        offers = [
            {
                "code": o.get("code") or "",
                "label": o.get("label") or o.get("code") or "",
                "slug": o.get("slug") or "",
            }
            for o in (self.offers or [])
        ]
        # show_title / slug / genre / venue / url are deliberately absent:
        # every performance is nested under its show in latest.json, which
        # already carries all five. Repeating them per performance was ~40%
        # of the payload.
        return {
            "performance_id": self.performance_id,
            "date": self.date_local,
            "time": self.time_local,
            "datetime_utc": self.datetime_utc,
            "ticket_status": self.ticket_status,
            "availability": self.availability,
            "percent_remaining": self.percent_remaining,
            "availability_level": self.availability_level,
            "box_office_id": self.box_office_id,
            "offers": offers,
        }
