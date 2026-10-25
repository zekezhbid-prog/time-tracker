from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class FakeClock:
    """Controllable "now" so durations are exact and tests don't sleep."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "tracker.db"


@pytest.fixture
def client(db_path, clock):
    return TestClient(create_app(db_path, clock))


def add(client, name="Иван Петров"):
    res = client.post("/api/employees", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


def report(client, start, end, **params):
    res = client.get(
        "/api/report",
        params={"start": start.isoformat(), "end": end.isoformat(), **params},
    )
    assert res.status_code == 200, res.text
    return res.json()


# --- employees -------------------------------------------------------------


def test_add_and_list_employees(client):
    add(client, "Мария")
    add(client, "Алексей")
    names = [e["name"] for e in client.get("/api/employees").json()]
    assert names == ["Алексей", "Мария"]


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_name_rejected(client, name):
    res = client.post("/api/employees", json={"name": name})
    assert res.status_code == 400


def test_duplicate_name_rejected_case_insensitive(client):
    add(client, "Мария")
    res = client.post("/api/employees", json={"name": "мария"})
    assert res.status_code == 409


# --- timer -----------------------------------------------------------------


def test_start_and_stop_timer(client, clock):
    emp = add(client)
    started = client.post(f"/api/employees/{emp['id']}/start", json={"task": "Отчёт"})
    assert started.status_code == 201
    assert started.json()["running"] is True

    clock.advance(minutes=45)
    stopped = client.post(f"/api/employees/{emp['id']}/stop").json()
    assert stopped["running"] is False
    assert stopped["seconds"] == 45 * 60


def test_second_start_while_running_is_rejected(client, clock):
    emp = add(client)
    client.post(f"/api/employees/{emp['id']}/start", json={"task": "Первая"})
    clock.advance(minutes=10)

    res = client.post(f"/api/employees/{emp['id']}/start", json={"task": "Вторая"})
    assert res.status_code == 409
    assert "Первая" in res.json()["detail"]

    running = client.get("/api/employees").json()[0]["running"]
    assert running["task"] == "Первая"


def test_restart_after_stop_creates_new_entry(client, clock):
    emp = add(client)
    for task, minutes in [("Утро", 30), ("После обеда", 20)]:
        client.post(f"/api/employees/{emp['id']}/start", json={"task": task})
        clock.advance(minutes=minutes)
        client.post(f"/api/employees/{emp['id']}/stop")
        clock.advance(minutes=5)

    data = report(client, T0, T0 + timedelta(days=1))
    assert [e["task"] for e in data["entries"]] == ["Утро", "После обеда"]
    assert data["totals"][0]["seconds"] == 50 * 60


def test_stop_without_running_timer(client):
    emp = add(client)
    assert client.post(f"/api/employees/{emp['id']}/stop").status_code == 409


def test_start_requires_task_description(client):
    emp = add(client)
    res = client.post(f"/api/employees/{emp['id']}/start", json={"task": "  "})
    assert res.status_code == 400


def test_unknown_employee(client):
    assert client.post("/api/employees/999/start", json={"task": "x"}).status_code == 404
    assert client.post("/api/employees/999/stop").status_code == 404


def test_timers_are_independent_per_employee(client, clock):
    a, b = add(client, "Анна"), add(client, "Борис")
    assert client.post(f"/api/employees/{a['id']}/start", json={"task": "A"}).status_code == 201
    assert client.post(f"/api/employees/{b['id']}/start", json={"task": "B"}).status_code == 201


# --- report ----------------------------------------------------------------


def test_empty_report_without_employees(client):
    data = report(client, T0, T0 + timedelta(days=7))
    assert data == {
        "from": T0.isoformat(),
        "to": (T0 + timedelta(days=7)).isoformat(),
        "totals": [],
        "total_seconds": 0,
        "entries": [],
    }


def test_empty_report_lists_employees_with_zero(client):
    add(client, "Мария")
    data = report(client, T0, T0 + timedelta(days=7))
    assert data["entries"] == []
    assert data["totals"] == [{"employee_id": 1, "name": "Мария", "seconds": 0}]


def test_report_excludes_entries_outside_period(client, clock):
    emp = add(client)
    client.post(f"/api/employees/{emp['id']}/start", json={"task": "Вчера"})
    clock.advance(hours=1)
    client.post(f"/api/employees/{emp['id']}/stop")

    next_day = T0 + timedelta(days=1)
    data = report(client, next_day, next_day + timedelta(days=1))
    assert data["entries"] == []
    assert data["total_seconds"] == 0


def test_report_clips_entry_crossing_period_boundary(client, clock):
    emp = add(client)
    clock.now = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)
    client.post(f"/api/employees/{emp['id']}/start", json={"task": "Ночной релиз"})
    clock.advance(hours=3)  # until 02:00 next day
    client.post(f"/api/employees/{emp['id']}/stop")

    day1 = datetime(2026, 10, 20, tzinfo=timezone.utc)
    day2 = day1 + timedelta(days=1)
    assert report(client, day1, day2)["total_seconds"] == 1 * 3600
    assert report(client, day2, day2 + timedelta(days=1))["total_seconds"] == 2 * 3600


def test_report_counts_running_timer_up_to_now(client, clock):
    emp = add(client)
    client.post(f"/api/employees/{emp['id']}/start", json={"task": "В процессе"})
    clock.advance(minutes=25)

    data = report(client, T0, T0 + timedelta(days=1))
    assert data["entries"][0]["running"] is True
    assert data["total_seconds"] == 25 * 60


def test_report_filtered_by_employee(client, clock):
    a, b = add(client, "Анна"), add(client, "Борис")
    for emp in (a, b):
        client.post(f"/api/employees/{emp['id']}/start", json={"task": emp["name"]})
    clock.advance(minutes=10)

    data = report(client, T0, T0 + timedelta(days=1), employee_id=b["id"])
    assert [e["employee_name"] for e in data["entries"]] == ["Борис"]
    assert [t["name"] for t in data["totals"]] == ["Борис"]


def test_report_rejects_inverted_period(client):
    res = client.get(
        "/api/report",
        params={"start": (T0 + timedelta(days=1)).isoformat(), "end": T0.isoformat()},
    )
    assert res.status_code == 400


def test_report_rejects_naive_datetime(client):
    res = client.get("/api/report", params={"start": "2026-10-20T00:00:00", "end": "2026-10-21T00:00:00"})
    assert res.status_code == 400


# --- persistence -----------------------------------------------------------


def test_data_survives_restart(db_path, clock):
    first = TestClient(create_app(db_path, clock))
    emp = add(first, "Мария")
    first.post(f"/api/employees/{emp['id']}/start", json={"task": "До перезапуска"})
    clock.advance(minutes=15)
    first.post(f"/api/employees/{emp['id']}/stop")
    first.post(f"/api/employees/{emp['id']}/start", json={"task": "Таймер не остановлен"})

    # A brand-new app instance on the same database file = application restart.
    second = TestClient(create_app(db_path, clock))
    employees = second.get("/api/employees").json()
    assert [e["name"] for e in employees] == ["Мария"]
    assert employees[0]["running"]["task"] == "Таймер не остановлен"

    clock.advance(minutes=5)
    data = report(second, T0, T0 + timedelta(days=1))
    assert [e["task"] for e in data["entries"]] == ["До перезапуска", "Таймер не остановлен"]
    assert data["total_seconds"] == 20 * 60


def test_ui_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Учёт рабочего времени" in res.text
