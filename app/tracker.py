"""Business logic: employees, timers and the period report."""

import sqlite3
from datetime import datetime, timezone

from .db import Database


class TrackerError(Exception):
    """Expected user-facing error (maps to a 4xx response)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise TrackerError("Время должно быть с часовым поясом (ISO-8601)")
    return dt.astimezone(timezone.utc)


class Tracker:
    def __init__(self, db: Database, clock=utcnow):
        self.db = db
        self.clock = clock

    # --- employees -----------------------------------------------------

    def add_employee(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise TrackerError("Имя сотрудника не может быть пустым")
        if len(name) > 100:
            raise TrackerError("Имя длиннее 100 символов")
        try:
            with self.db.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO employees(name, name_key, created_at) VALUES (?, ?, ?)",
                    (name, " ".join(name.casefold().split()), to_iso(self.clock())),
                )
                return self._employee(conn, cur.lastrowid)
        except sqlite3.IntegrityError:
            raise TrackerError(f"Сотрудник «{name}» уже есть", 409)

    def list_employees(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id FROM employees ORDER BY name").fetchall()
            return [self._employee(conn, r["id"]) for r in rows]

    def _employee(self, conn, employee_id: int) -> dict:
        row = conn.execute(
            "SELECT id, name FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if row is None:
            raise TrackerError("Сотрудник не найден", 404)
        running = conn.execute(
            "SELECT * FROM time_entries WHERE employee_id = ? AND stopped_at IS NULL",
            (employee_id,),
        ).fetchone()
        return {
            "id": row["id"],
            "name": row["name"],
            "running": self._entry(running) if running else None,
        }

    # --- timer ---------------------------------------------------------

    def start(self, employee_id: int, task: str) -> dict:
        task = (task or "").strip()
        if not task:
            raise TrackerError("Опишите задачу перед запуском таймера")
        if len(task) > 500:
            raise TrackerError("Описание задачи длиннее 500 символов")
        with self.db.connect() as conn:
            employee = self._employee(conn, employee_id)
            if employee["running"]:
                raise TrackerError(
                    "Таймер уже запущен: сначала остановите задачу "
                    f"«{employee['running']['task']}»",
                    409,
                )
            try:
                cur = conn.execute(
                    "INSERT INTO time_entries(employee_id, task, started_at) VALUES (?, ?, ?)",
                    (employee_id, task, to_iso(self.clock())),
                )
            except sqlite3.IntegrityError:
                # Two concurrent starts: the partial unique index rejects the second one.
                raise TrackerError("Таймер уже запущен", 409)
            row = conn.execute(
                "SELECT * FROM time_entries WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return self._entry(row)

    def stop(self, employee_id: int) -> dict:
        with self.db.connect() as conn:
            employee = self._employee(conn, employee_id)
            if not employee["running"]:
                raise TrackerError("Таймер не запущен", 409)
            entry_id = employee["running"]["id"]
            conn.execute(
                "UPDATE time_entries SET stopped_at = ? WHERE id = ?",
                (to_iso(self.clock()), entry_id),
            )
            row = conn.execute(
                "SELECT * FROM time_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            return self._entry(row)

    # --- report --------------------------------------------------------

    def report(self, start: str, end: str, employee_id: int | None = None) -> dict:
        """Entries overlapping [start, end) and totals per employee.

        Durations are clipped to the period, so a task that crosses midnight
        is split correctly between days. A running timer counts up to "now".
        """
        period_start, period_end = parse_iso(start), parse_iso(end)
        if period_end <= period_start:
            raise TrackerError("Конец периода должен быть позже начала")
        now = self.clock()

        query = """
            SELECT e.*, emp.name AS employee_name
            FROM time_entries e JOIN employees emp ON emp.id = e.employee_id
            WHERE e.started_at < ? AND (e.stopped_at IS NULL OR e.stopped_at > ?)
        """
        params: list = [to_iso(period_end), to_iso(period_start)]
        if employee_id is not None:
            query += " AND e.employee_id = ?"
            params.append(employee_id)
        query += " ORDER BY e.started_at"

        with self.db.connect() as conn:
            if employee_id is not None:
                self._employee(conn, employee_id)  # 404 for unknown employee
            rows = conn.execute(query, params).fetchall()
            employees = conn.execute(
                "SELECT id, name FROM employees"
                + (" WHERE id = ?" if employee_id is not None else "")
                + " ORDER BY name",
                [employee_id] if employee_id is not None else [],
            ).fetchall()

        totals = {e["id"]: 0 for e in employees}
        entries = []
        for row in rows:
            entry = self._entry(row, now)
            entry["employee_name"] = row["employee_name"]
            started = parse_iso(row["started_at"])
            stopped = parse_iso(row["stopped_at"]) if row["stopped_at"] else now
            clipped = (min(stopped, period_end) - max(started, period_start)).total_seconds()
            entry["seconds_in_period"] = max(0, int(clipped))
            totals[row["employee_id"]] += entry["seconds_in_period"]
            entries.append(entry)

        return {
            "from": to_iso(period_start),
            "to": to_iso(period_end),
            "totals": [
                {"employee_id": e["id"], "name": e["name"], "seconds": totals[e["id"]]}
                for e in employees
            ],
            "total_seconds": sum(totals.values()),
            "entries": entries,
        }

    def _entry(self, row, now: datetime | None = None) -> dict:
        now = now or self.clock()
        started = parse_iso(row["started_at"])
        stopped = parse_iso(row["stopped_at"]) if row["stopped_at"] else None
        return {
            "id": row["id"],
            "employee_id": row["employee_id"],
            "task": row["task"],
            "started_at": row["started_at"],
            "stopped_at": row["stopped_at"],
            "running": stopped is None,
            "seconds": int(((stopped or now) - started).total_seconds()),
        }
