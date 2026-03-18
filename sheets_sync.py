"""
Google Sheets sync for Beautiful Feet Evangelism Heatmap.

Column mapping (1-indexed):
  A=person_name, B=prayer_level, C=evangelisers, D=status,
  E=date_of_evangelism (DD/MM/YYYY), F=date_of_accepting_christ,
  G=(empty), H=notes, I=phone_numbers,
  J=location_area, K=latitude, L=longitude,
  M=follow_up_status, N=record_id,
  O=outing_day, P=outing_date (DD/MM/YYYY)
"""

import json
import os
import uuid
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

EXPECTED_HEADERS = {
    10: "location_area",    # J
    11: "latitude",         # K
    12: "longitude",        # L
    13: "follow_up_status", # M
    14: "record_id",        # N
    15: "outing_day",       # O
    16: "outing_date",      # P
}

_client: gspread.Client | None = None
_sheet: gspread.Worksheet | None = None


def _get_sheet() -> gspread.Worksheet:
    global _client, _sheet
    if _sheet is not None:
        return _sheet

    sheet_id = os.environ["GOOGLE_SHEET_ID"]

    # Support both a JSON string (for cloud deployment) and a file path (for local dev)
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds_path = os.environ["GOOGLE_CREDENTIALS_PATH"]
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

    _client = gspread.authorize(creds)
    spreadsheet = _client.open_by_key(sheet_id)
    _sheet = spreadsheet.sheet1
    _ensure_headers(_sheet)
    return _sheet


def _ensure_headers(sheet: gspread.Worksheet) -> None:
    """Ensure columns J–N have the expected headers in row 1."""
    headers = sheet.row_values(1)
    updates = []
    for col_idx, header_name in EXPECTED_HEADERS.items():
        # col_idx is 1-based
        current = headers[col_idx - 1] if len(headers) >= col_idx else ""
        if not current:
            updates.append({
                "range": gspread.utils.rowcol_to_a1(1, col_idx),
                "values": [[header_name]],
            })
    if updates:
        sheet.spreadsheet.values_batch_update(
            {
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": u["range"], "values": u["values"]}
                    for u in updates
                ],
            }
        )


def _format_date(d) -> str:
    if d is None:
        return ""
    if isinstance(d, (date, datetime)):
        return d.strftime("%d/%m/%Y")
    return str(d)


def _parse_date(s: str):
    """Parse DD/MM/YYYY → date, return None on failure."""
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def append_visit(data: dict) -> None:
    """Append a single visit as a new row in the sheet."""
    sheet = _get_sheet()
    row = [
        data.get("person_name", ""),
        data.get("prayer_level", ""),
        data.get("evangelisers", ""),
        data.get("status", ""),
        _format_date(data.get("date_of_evangelism")),
        _format_date(data.get("date_of_accepting_christ")),
        "",  # G — empty
        data.get("notes", "") or "",
        data.get("phone_numbers", "") or "",
        data.get("location_area", "") or "",
        str(data.get("latitude", "")) if data.get("latitude") is not None else "",
        str(data.get("longitude", "")) if data.get("longitude") is not None else "",
        data.get("follow_up_status", "New"),
        str(data.get("record_id", "")),
        data.get("outing_day", "") or "",
        _format_date(data.get("outing_date")),
    ]
    sheet.append_row(row, value_input_option="USER_ENTERED")


def update_row(record_id: str, updates: dict) -> None:
    """Find row where col N == record_id and update specified columns."""
    sheet = _get_sheet()
    col_n_values = sheet.col_values(14)  # column N (1-indexed = 14)

    row_idx = None
    for i, val in enumerate(col_n_values):
        if val.strip() == record_id.strip():
            row_idx = i + 1  # 1-indexed
            break

    if row_idx is None:
        raise ValueError(f"record_id {record_id} not found in sheet")

    col_map = {
        "person_name": 1,
        "prayer_level": 2,
        "evangelisers": 3,
        "status": 4,
        "date_of_evangelism": 5,
        "date_of_accepting_christ": 6,
        "notes": 8,
        "phone_numbers": 9,
        "location_area": 10,
        "latitude": 11,
        "longitude": 12,
        "follow_up_status": 13,
        "outing_day": 15,
        "outing_date": 16,
    }

    batch = []
    for field, value in updates.items():
        col_idx = col_map.get(field)
        if col_idx is None:
            continue
        if field in ("date_of_evangelism", "date_of_accepting_christ"):
            value = _format_date(value)
        else:
            value = "" if value is None else str(value)
        batch.append({
            "range": gspread.utils.rowcol_to_a1(row_idx, col_idx),
            "values": [[value]],
        })

    if batch:
        sheet.spreadsheet.values_batch_update(
            {
                "valueInputOption": "USER_ENTERED",
                "data": [{"range": b["range"], "values": b["values"]} for b in batch],
            }
        )


def sync_existing_to_db(db_session) -> int:
    """
    Read all sheet rows (skip header), upsert into visits table.
    Returns count of rows processed.
    """
    from main import Visit  # local import to avoid circular deps

    sheet = _get_sheet()
    all_rows = sheet.get_all_values()
    if not all_rows:
        return 0

    # Skip header row
    data_rows = all_rows[1:]
    count = 0

    for row in data_rows:
        # Pad row to 14 columns
        row = row + [""] * (14 - len(row))

        person_name = row[0].strip()
        if not person_name:
            continue

        prayer_level = row[1].strip() or "Low"
        evangelisers = row[2].strip()
        status = row[3].strip() or "Unsaved"
        date_of_evangelism = _parse_date(row[4])
        date_of_accepting_christ = _parse_date(row[5])
        notes = row[7].strip() or None
        phone_numbers = row[8].strip() or None
        location_area = row[9].strip() or None
        lat_str = row[10].strip()
        lng_str = row[11].strip()
        follow_up_status = row[12].strip() or "New"
        record_id_str = row[13].strip()

        if not record_id_str:
            record_id_str = str(uuid.uuid4())

        try:
            lat = float(lat_str) if lat_str else None
            lng = float(lng_str) if lng_str else None
        except ValueError:
            lat = lng = None

        if date_of_evangelism is None:
            from datetime import date as date_cls
            date_of_evangelism = date_cls.today()

        # Check existing
        existing = (
            db_session.query(Visit)
            .filter(Visit.record_id == record_id_str)
            .first()
        )

        if existing:
            existing.person_name = person_name
            existing.prayer_level = prayer_level
            existing.evangelisers = evangelisers
            existing.status = status
            existing.date_of_evangelism = date_of_evangelism
            existing.date_of_accepting_christ = date_of_accepting_christ
            existing.notes = notes
            existing.phone_numbers = phone_numbers
            existing.location_area = location_area
            existing.latitude = lat_str if lat is not None else None
            existing.longitude = lng_str if lng is not None else None
            existing.follow_up_status = follow_up_status
            existing.sheet_synced = True
            if lat is not None and lng is not None:
                from geoalchemy2.elements import WKTElement
                existing.geom = WKTElement(f"POINT({lng} {lat})", srid=4326)
        else:
            from geoalchemy2.elements import WKTElement
            geom = WKTElement(f"POINT({lng} {lat})", srid=4326) if (lat is not None and lng is not None) else None
            visit = Visit(
                record_id=record_id_str,
                person_name=person_name,
                prayer_level=prayer_level,
                evangelisers=evangelisers,
                status=status,
                date_of_evangelism=date_of_evangelism,
                date_of_accepting_christ=date_of_accepting_christ,
                notes=notes,
                phone_numbers=phone_numbers,
                location_area=location_area,
                latitude=lat_str if lat is not None else None,
                longitude=lng_str if lng is not None else None,
                follow_up_status=follow_up_status,
                sheet_synced=True,
                geom=geom,
            )
            db_session.add(visit)

        count += 1

    db_session.commit()
    return count
