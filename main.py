"""
Beautiful Feet Evangelism Heatmap — FastAPI backend
"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    text,
    func,
    and_,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.future import select

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(String(36), unique=True, nullable=False, index=True)
    person_name = Column(String(255), nullable=False)
    prayer_level = Column(String(50), nullable=False)
    evangelisers = Column(Text, nullable=False)
    status = Column(String(50), nullable=False)
    date_of_evangelism = Column(Date, nullable=False)
    date_of_accepting_christ = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    phone_numbers = Column(String(255), nullable=True)
    location_area = Column(String(255), nullable=True)
    latitude = Column(String(50), nullable=True)
    longitude = Column(String(50), nullable=True)
    follow_up_status = Column(String(50), default="New")
    team_name = Column(String(100), nullable=True)
    outing_day = Column(String(20), nullable=True)   # Monday–Sunday
    outing_date = Column(Date, nullable=True)         # actual date of the outing session
    sheet_synced = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))


class SyncFailure(Base):
    __tablename__ = "sync_failures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(String(36), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    resolved_at = Column(DateTime(timezone=True), nullable=True)


# ── Pydantic models ──────────────────────────────────────────────────────────

class VisitCreate(BaseModel):
    person_name: str
    prayer_level: str
    evangelisers: str
    status: str
    date_of_evangelism: Optional[str] = None  # DD/MM/YYYY
    date_of_accepting_christ: Optional[str] = None
    notes: Optional[str] = None
    phone_numbers: Optional[str] = None
    location_area: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    follow_up_status: Optional[str] = "New"
    team_name: Optional[str] = None
    outing_day: Optional[str] = None   # e.g. "Monday"
    outing_date: Optional[str] = None  # DD/MM/YYYY

    @field_validator("date_of_evangelism", "date_of_accepting_christ", "outing_date", mode="before")
    @classmethod
    def parse_date_field(cls, v):
        return v  # keep as string; we parse below


class VisitUpdate(BaseModel):
    status: Optional[str] = None
    follow_up_status: Optional[str] = None
    notes: Optional[str] = None
    prayer_level: Optional[str] = None
    date_of_accepting_christ: Optional[str] = None


class VisitResponse(BaseModel):
    id: int
    record_id: str
    person_name: str
    prayer_level: str
    evangelisers: str
    status: str
    date_of_evangelism: Optional[date]
    date_of_accepting_christ: Optional[date]
    notes: Optional[str]
    phone_numbers: Optional[str]
    location_area: Optional[str]
    latitude: Optional[str]
    longitude: Optional[str]
    follow_up_status: Optional[str]
    team_name: Optional[str]
    outing_day: Optional[str]
    outing_date: Optional[date]
    sheet_synced: bool
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


# ── Database session dependency ──────────────────────────────────────────────

async def get_db():
    async with async_session_factory() as session:
        yield session


# ── Lifespan ──────────────────────────────────────────────────────────────────

import sheets_sync as _sheets_sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # warm up sheets client (optional — will init lazily on first use)
    yield


app = FastAPI(title="Beautiful Feet Evangelism Heatmap", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Background tasks ──────────────────────────────────────────────────────────

async def _sync_to_sheet(record_id: str, data: dict):
    try:
        await asyncio.to_thread(_sheets_sync.append_visit, data)
        async with async_session_factory() as session:
            result = await session.execute(
                select(Visit).where(Visit.record_id == record_id)
            )
            visit = result.scalar_one_or_none()
            if visit:
                visit.sheet_synced = True
                await session.commit()
    except Exception as exc:
        async with async_session_factory() as session:
            failure = SyncFailure(
                record_id=record_id,
                error_message=str(exc),
                retry_count=0,
            )
            session.add(failure)
            await session.commit()


async def _sync_update_to_sheet(record_id: str, updates: dict):
    try:
        await asyncio.to_thread(_sheets_sync.update_row, record_id, updates)
    except Exception:
        pass  # non-critical for updates


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/api/visits", response_model=VisitResponse, status_code=201)
async def create_visit(
    body: VisitCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    record_id = str(uuid.uuid4())

    doe = _parse_date(body.date_of_evangelism) or date.today()
    doac = _parse_date(body.date_of_accepting_christ)

    lat = body.latitude
    lng = body.longitude

    visit = Visit(
        record_id=record_id,
        person_name=body.person_name,
        prayer_level=body.prayer_level,
        evangelisers=body.evangelisers,
        status=body.status,
        date_of_evangelism=doe,
        date_of_accepting_christ=doac,
        notes=body.notes,
        phone_numbers=body.phone_numbers,
        location_area=body.location_area,
        latitude=lat,
        longitude=lng,
        follow_up_status=body.follow_up_status or "New",
        team_name=body.team_name,
        outing_day=body.outing_day,
        outing_date=_parse_date(body.outing_date),
        sheet_synced=False,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    sheet_data = {
        "record_id": record_id,
        "person_name": body.person_name,
        "prayer_level": body.prayer_level,
        "evangelisers": body.evangelisers,
        "status": body.status,
        "date_of_evangelism": doe,
        "date_of_accepting_christ": doac,
        "notes": body.notes,
        "phone_numbers": body.phone_numbers,
        "location_area": body.location_area,
        "latitude": lat,
        "longitude": lng,
        "follow_up_status": body.follow_up_status or "New",
        "outing_day": body.outing_day,
        "outing_date": _parse_date(body.outing_date),
    }
    background_tasks.add_task(_sync_to_sheet, record_id, sheet_data)

    return visit


@app.get("/api/visits", response_model=list[VisitResponse])
async def list_visits(
    team: Optional[str] = None,
    evangeliser: Optional[str] = None,
    status: Optional[str] = None,
    follow_up_status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Visit)
    filters = []

    if team:
        filters.append(Visit.outing_day == team)
    if evangeliser:
        filters.append(Visit.evangelisers.ilike(f"%{evangeliser}%"))
    if status:
        filters.append(Visit.status == status)
    if follow_up_status:
        filters.append(Visit.follow_up_status == follow_up_status)
    if from_date:
        d = _parse_date(from_date)
        if d:
            filters.append(Visit.date_of_evangelism >= d)
    if to_date:
        d = _parse_date(to_date)
        if d:
            filters.append(Visit.date_of_evangelism <= d)

    if filters:
        stmt = stmt.where(and_(*filters))

    stmt = stmt.order_by(Visit.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(stmt)
    return result.scalars().all()


@app.get("/api/heatmap")
async def get_heatmap(
    team: Optional[str] = None,
    precision: int = Query(4, ge=1, le=6),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Visit).where(
        Visit.latitude.isnot(None),
        Visit.longitude.isnot(None),
    )
    if team:
        stmt = stmt.where(Visit.outing_day == team)

    result = await db.execute(stmt)
    visits = result.scalars().all()

    counts: dict[tuple, int] = {}
    for v in visits:
        try:
            lat = round(float(v.latitude), precision)
            lng = round(float(v.longitude), precision)
            counts[(lat, lng)] = counts.get((lat, lng), 0) + 1
        except (ValueError, TypeError):
            pass

    return [
        {"latitude": lat, "longitude": lng, "intensity": cnt}
        for (lat, lng), cnt in counts.items()
    ]


@app.get("/api/visits/geojson")
async def get_geojson(db: AsyncSession = Depends(get_db)):
    stmt = select(Visit).where(
        Visit.latitude.isnot(None),
        Visit.longitude.isnot(None),
    )
    result = await db.execute(stmt)
    visits = result.scalars().all()

    features = []
    for v in visits:
        try:
            lat = float(v.latitude)
            lng = float(v.longitude)
        except (ValueError, TypeError):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "record_id": v.record_id,
                "person_name": v.person_name,
                "status": v.status,
                "evangelisers": v.evangelisers,
                "date_of_evangelism": v.date_of_evangelism.isoformat() if v.date_of_evangelism else None,
                "follow_up_status": v.follow_up_status,
                "team_name": v.team_name,
            },
        })

    return {"type": "FeatureCollection", "features": features}


@app.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Visit))
    visits = result.scalars().all()

    total_visits = len(visits)
    total_saved = sum(1 for v in visits if v.status == "Saved")
    total_unsaved = sum(1 for v in visits if v.status == "Unsaved")
    total_being_discipled = sum(1 for v in visits if v.status == "Being discipled")

    all_evangelisers: set[str] = set()
    all_teams: set[str] = set()
    for v in visits:
        if v.evangelisers:
            for name in v.evangelisers.split(","):
                n = name.strip()
                if n:
                    all_evangelisers.add(n)
        if v.team_name:
            all_teams.add(v.team_name)

    return {
        "total_visits": total_visits,
        "total_saved": total_saved,
        "total_unsaved": total_unsaved,
        "total_being_discipled": total_being_discipled,
        "total_evangelisers": len(all_evangelisers),
        "total_teams": len(all_teams),
    }


@app.get("/api/teams")
async def get_teams(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Visit.team_name).where(Visit.team_name.isnot(None)).distinct()
    )
    return sorted([r[0] for r in result.all() if r[0]])


@app.get("/api/evangelisers")
async def get_evangelisers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Visit.evangelisers))
    names: set[str] = set()
    for (evs,) in result.all():
        if evs:
            for name in evs.split(","):
                n = name.strip()
                if n:
                    names.add(n)
    return sorted(names)


@app.put("/api/visits/{record_id}", response_model=VisitResponse)
async def update_visit(
    record_id: str,
    body: VisitUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Visit).where(Visit.record_id == record_id))
    visit = result.scalar_one_or_none()
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")

    updates: dict = {}
    if body.status is not None:
        visit.status = body.status
        updates["status"] = body.status
    if body.follow_up_status is not None:
        visit.follow_up_status = body.follow_up_status
        updates["follow_up_status"] = body.follow_up_status
    if body.notes is not None:
        visit.notes = body.notes
        updates["notes"] = body.notes
    if body.prayer_level is not None:
        visit.prayer_level = body.prayer_level
        updates["prayer_level"] = body.prayer_level
    if body.date_of_accepting_christ is not None:
        doac = _parse_date(body.date_of_accepting_christ)
        visit.date_of_accepting_christ = doac
        updates["date_of_accepting_christ"] = doac

    await db.commit()
    await db.refresh(visit)

    if updates:
        background_tasks.add_task(_sync_update_to_sheet, record_id, updates)

    return visit


@app.post("/api/sync/retry")
async def retry_sync(db: AsyncSession = Depends(get_db)):
    # Find unsynced visits
    result = await db.execute(
        select(Visit).where(Visit.sheet_synced == False)
    )
    unsynced = result.scalars().all()

    success_count = 0
    fail_count = 0

    # Also resolve old failures
    failures_result = await db.execute(
        select(SyncFailure).where(SyncFailure.resolved_at.is_(None))
    )
    old_failures = {f.record_id: f for f in failures_result.scalars().all()}

    for visit in unsynced:
        data = {
            "record_id": visit.record_id,
            "person_name": visit.person_name,
            "prayer_level": visit.prayer_level,
            "evangelisers": visit.evangelisers,
            "status": visit.status,
            "date_of_evangelism": visit.date_of_evangelism,
            "date_of_accepting_christ": visit.date_of_accepting_christ,
            "notes": visit.notes,
            "phone_numbers": visit.phone_numbers,
            "location_area": visit.location_area,
            "latitude": visit.latitude,
            "longitude": visit.longitude,
            "follow_up_status": visit.follow_up_status,
        }
        try:
            await asyncio.to_thread(_sheets_sync.append_visit, data)
            visit.sheet_synced = True
            success_count += 1
            # resolve failure if exists
            if visit.record_id in old_failures:
                old_failures[visit.record_id].resolved_at = datetime.utcnow()
        except Exception as exc:
            fail_count += 1
            if visit.record_id in old_failures:
                old_failures[visit.record_id].retry_count += 1
            else:
                failure = SyncFailure(
                    record_id=visit.record_id,
                    error_message=str(exc),
                    retry_count=1,
                )
                db.add(failure)

    await db.commit()
    return {"synced": success_count, "failed": fail_count}


@app.post("/api/sync/import")
async def import_from_sheet():
    from sqlalchemy.orm import Session as SyncSession
    from sqlalchemy import create_engine as sync_create_engine

    sync_db_url = DATABASE_URL.replace("+asyncpg", "+psycopg2")
    sync_engine = sync_create_engine(sync_db_url)

    with SyncSession(sync_engine) as session:
        count = _sheets_sync.sync_existing_to_db(session)

    return {"imported": count}


# ── Static files (frontend) ───────────────────────────────────────────────────

frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
