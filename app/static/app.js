const $ = (sel) => document.querySelector(sel);
let employees = [];

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? "Проверьте введённые данные" : data.detail;
    throw new Error(detail || `Ошибка ${res.status}`);
  }
  return data;
}

function showError(err) {
  const box = $("#error");
  box.textContent = err ? err.message : "";
  box.hidden = !err;
}

async function run(action) {
  try {
    showError(null);
    await action();
  } catch (err) {
    showError(err);
  }
}

function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function formatTime(iso) {
  return iso ? new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function elapsed(entry) {
  return (Date.now() - new Date(entry.started_at).getTime()) / 1000;
}

// --- employees -------------------------------------------------------------

async function loadEmployees() {
  employees = await api("/api/employees");
  renderEmployees();
  renderEmployeeFilter();
}

function renderEmployees() {
  const list = $("#employees");
  list.replaceChildren();
  $("#no-employees").hidden = employees.length > 0;

  for (const emp of employees) {
    const row = $("#employee-row").content.firstElementChild.cloneNode(true);
    row.querySelector(".name").textContent = emp.name;
    const status = row.querySelector(".status");
    const form = row.querySelector(".timer");
    const input = form.elements.task;
    const running = emp.running;

    status.dataset.started = running ? running.started_at : "";
    status.classList.toggle("active", Boolean(running));
    status.textContent = running ? `⏱ ${formatDuration(elapsed(running))}` : "не работает";
    input.value = running ? running.task : "";
    input.disabled = Boolean(running);
    form.querySelector(".start").disabled = Boolean(running);
    form.querySelector(".stop").disabled = !running;

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      run(async () => {
        await api(`/api/employees/${emp.id}/start`, { method: "POST", body: { task: input.value } });
        await loadEmployees();
      });
    });
    form.querySelector(".stop").addEventListener("click", () =>
      run(async () => {
        await api(`/api/employees/${emp.id}/stop`, { method: "POST" });
        await loadEmployees();
      })
    );
    list.append(row);
  }
}

function renderEmployeeFilter() {
  const select = $("#report-form").elements.employee;
  const current = select.value;
  select.replaceChildren(new Option("Все", ""));
  for (const emp of employees) select.append(new Option(emp.name, emp.id));
  select.value = employees.some((e) => String(e.id) === current) ? current : "";
}

$("#add-employee").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  run(async () => {
    await api("/api/employees", { method: "POST", body: { name: form.elements.name.value } });
    form.reset();
    await loadEmployees();
  });
});

// Live counters for running timers.
setInterval(() => {
  for (const el of document.querySelectorAll(".status[data-started]")) {
    if (el.dataset.started) {
      el.textContent = `⏱ ${formatDuration((Date.now() - new Date(el.dataset.started)) / 1000)}`;
    }
  }
}, 1000);

// --- report ----------------------------------------------------------------

function localDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The "to" date is inclusive for the user, so the period ends at the next local midnight.
function periodToIso(fromValue, toValue) {
  const start = new Date(`${fromValue}T00:00:00`);
  const end = new Date(`${toValue}T00:00:00`);
  end.setDate(end.getDate() + 1);
  return { start: start.toISOString(), end: end.toISOString() };
}

async function loadReport() {
  const form = $("#report-form");
  const { start, end } = periodToIso(form.elements.from.value, form.elements.to.value);
  const params = new URLSearchParams({ start, end });
  if (form.elements.employee.value) params.set("employee_id", form.elements.employee.value);
  renderReport(await api(`/api/report?${params}`));
}

function renderReport(report) {
  const box = $("#report");
  if (report.entries.length === 0) {
    box.innerHTML = '<p class="muted">За выбранный период записей нет.</p>';
    return;
  }

  const totals = document.createElement("table");
  totals.innerHTML = "<thead><tr><th>Сотрудник</th><th class='num'>Всего за период</th></tr></thead>";
  const tbody = totals.createTBody();
  for (const t of report.totals) {
    const tr = tbody.insertRow();
    tr.insertCell().textContent = t.name;
    const cell = tr.insertCell();
    cell.className = "num";
    cell.textContent = formatDuration(t.seconds);
  }

  const entries = document.createElement("table");
  entries.innerHTML =
    "<thead><tr><th>Сотрудник</th><th>Задача</th><th>Начало</th><th>Конец</th><th class='num'>В периоде</th></tr></thead>";
  const body = entries.createTBody();
  for (const e of report.entries) {
    const tr = body.insertRow();
    tr.insertCell().textContent = e.employee_name;
    tr.insertCell().textContent = e.task;
    tr.insertCell().textContent = formatTime(e.started_at);
    tr.insertCell().textContent = e.running ? "идёт" : formatTime(e.stopped_at);
    const cell = tr.insertCell();
    cell.className = "num";
    cell.textContent = formatDuration(e.seconds_in_period);
  }

  box.replaceChildren(totals, entries);
}

$("#report-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(loadReport);
});

// --- init ------------------------------------------------------------------

(function init() {
  const today = new Date();
  const weekAgo = new Date(today);
  weekAgo.setDate(today.getDate() - 6);
  const form = $("#report-form");
  form.elements.from.value = localDate(weekAgo);
  form.elements.to.value = localDate(today);
  run(loadEmployees);
})();
