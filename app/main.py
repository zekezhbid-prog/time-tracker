"""HTTP API and static UI."""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import Database
from .tracker import Tracker, TrackerError

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "tracker.db"


class EmployeeIn(BaseModel):
    name: str


class StartIn(BaseModel):
    task: str


def create_app(db_path: str | Path | None = None, clock=None) -> FastAPI:
    db = Database(db_path or os.environ.get("TRACKER_DB", DEFAULT_DB))
    tracker = Tracker(db, clock) if clock else Tracker(db)

    app = FastAPI(title="Учёт рабочего времени")

    @app.exception_handler(TrackerError)
    async def tracker_error(_: Request, exc: TrackerError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.get("/api/employees")
    def list_employees():
        return tracker.list_employees()

    @app.post("/api/employees", status_code=201)
    def add_employee(body: EmployeeIn):
        return tracker.add_employee(body.name)

    @app.post("/api/employees/{employee_id}/start", status_code=201)
    def start(employee_id: int, body: StartIn):
        return tracker.start(employee_id, body.task)

    @app.post("/api/employees/{employee_id}/stop")
    def stop(employee_id: int):
        return tracker.stop(employee_id)

    @app.get("/api/report")
    def report(start: str, end: str, employee_id: int | None = None):
        return tracker.report(start, end, employee_id)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
