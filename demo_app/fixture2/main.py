"""
demo_app/fixture2/main.py
==========================
VerityQA Phase 1 — Second Test Fixture

A task-management dashboard application that is STRUCTURALLY DIFFERENT
from the login/registration/search fixture (demo_app/main.py).

Structural differences:
  - No login or registration page
  - Tab-based navigation
  - Filterable/sortable task list
  - Modal dialog for task creation
  - Pagination
  - Three intentional planted defects for exploration validation

Runs on http://127.0.0.1:8002

Planted defects:
  BUG-F2-01: Task with empty title is accepted (should be rejected)
  BUG-F2-02: Priority filter shows ALL tasks regardless of selected priority
  BUG-F2-03: Marking a task complete does NOT update its displayed status
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Form, Query, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="TaskDash — VerityQA Fixture 2", version="1.0.0")

# ---------------------------------------------------------------------------
# In-memory task store
# ---------------------------------------------------------------------------

PRIORITIES = ("High", "Medium", "Low")
STATUSES = ("Open", "In Progress", "Done")


class Task:
    def __init__(self, title: str, description: str = "", priority: str = "Medium", status: str = "Open"):
        self.task_id = str(uuid.uuid4())[:8]
        self.title = title
        self.description = description
        self.priority = priority
        self.status = status
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M")


# Seed data
_tasks: List[Task] = [
    Task("Set up CI/CD pipeline", "Configure GitHub Actions for automated testing", "High"),
    Task("Write unit tests", "Cover all service layer methods", "High", "In Progress"),
    Task("Update README", "Add usage instructions and screenshots", "Medium"),
    Task("Fix pagination bug", "Page 2 shows duplicate entries", "High"),
    Task("Refactor auth module", "Extract JWT logic into a dedicated service", "Medium", "In Progress"),
    Task("Add dark mode", "Implement CSS theme toggle", "Low"),
    Task("Performance audit", "Profile slow database queries", "High"),
    Task("Update dependencies", "Bump all outdated packages", "Low", "Done"),
]

PAGE_SIZE = 3


def _filter_tasks(priority: str = "", status: str = "", page: int = 1) -> tuple:
    filtered = _tasks
    # BUG-F2-02: priority filter is intentionally broken — ignores the selected priority
    # Correct logic would be:
    #   if priority:
    #       filtered = [t for t in filtered if t.priority == priority]
    # Broken: filter is silently ignored
    if status:
        filtered = [t for t in filtered if t.status == status]
    total = len(filtered)
    start = (page - 1) * PAGE_SIZE
    return filtered[start:start + PAGE_SIZE], total


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

NAV_TABS = [
    ("Dashboard", "/"),
    ("All Tasks", "/tasks"),
    ("Create Task", "/create"),
    ("About", "/about"),
]


def _base_html(title: str, active: str, body: str) -> str:
    tabs_html = "".join(
        f'<a href="{href}" class="tab{" active" if name == active else ""}">{name}</a>'
        for name, href in NAV_TABS
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TaskDash — {title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; background: #f0f2f5; color: #1a1a2e; }}
  .header {{ background: #16213e; color: white; padding: 1rem 2rem; }}
  .tabs {{ display:flex; gap:.5rem; background:#0f3460; padding:.5rem 2rem; }}
  .tab {{ color:#ccc; text-decoration:none; padding:.4rem .9rem; border-radius:4px; }}
  .tab.active, .tab:hover {{ background:#e94560; color:white; }}
  .container {{ max-width:900px; margin:2rem auto; padding:0 1rem; }}
  .card {{ background:white; border-radius:8px; padding:1.5rem; margin-bottom:1rem;
           box-shadow:0 2px 4px rgba(0,0,0,.08); }}
  .badge {{ display:inline-block; padding:.2rem .5rem; border-radius:4px; font-size:.75rem; font-weight:600; }}
  .badge-High {{ background:#fee2e2; color:#dc2626; }}
  .badge-Medium {{ background:#fef3c7; color:#d97706; }}
  .badge-Low {{ background:#d1fae5; color:#059669; }}
  .badge-Open {{ background:#dbeafe; color:#2563eb; }}
  .badge-In.Progress, .badge-In\ Progress {{ background:#ede9fe; color:#7c3aed; }}
  .badge-Done {{ background:#d1fae5; color:#059669; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ background:#f8fafc; text-align:left; padding:.7rem; border-bottom:2px solid #e2e8f0; }}
  td {{ padding:.7rem; border-bottom:1px solid #f1f5f9; }}
  tr:hover {{ background:#fafafa; }}
  input, select, textarea {{ width:100%; padding:.5rem; border:1px solid #d1d5db;
                             border-radius:4px; margin-bottom:.75rem; box-sizing:border-box; }}
  button.primary {{ background:#e94560; color:white; border:none; padding:.6rem 1.4rem;
                    border-radius:4px; cursor:pointer; font-size:1rem; }}
  button.primary:hover {{ background:#c73652; }}
  button.secondary {{ background:#64748b; color:white; border:none; padding:.5rem 1rem;
                      border-radius:4px; cursor:pointer; }}
  .pagination {{ display:flex; gap:.5rem; margin-top:1rem; align-items:center; }}
  .pagination a {{ padding:.3rem .7rem; border:1px solid #cbd5e1; border-radius:4px;
                   text-decoration:none; color:#475569; }}
  .pagination a.current {{ background:#e94560; color:white; border-color:#e94560; }}
  .stat-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1rem; margin-bottom:1.5rem; }}
  .stat-card {{ background:white; border-radius:8px; padding:1rem; text-align:center;
                box-shadow:0 2px 4px rgba(0,0,0,.06); }}
  .stat-num {{ font-size:2rem; font-weight:700; color:#e94560; }}
  .error {{ color:#dc2626; font-size:.9rem; margin-top:.5rem; }}
  .success {{ color:#059669; font-weight:600; }}
  .filter-bar {{ display:flex; gap:1rem; align-items:center; margin-bottom:1rem; }}
  .filter-bar select {{ width:auto; margin-bottom:0; }}
  .filter-bar label {{ font-weight:600; white-space:nowrap; }}
</style>
</head>
<body>
<div class="header"><h1>📋 TaskDash</h1><p>Project Task Management Dashboard</p></div>
<div class="tabs">{tabs_html}</div>
<div class="container">{body}</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    total = len(_tasks)
    open_count = sum(1 for t in _tasks if t.status == "Open")
    done_count = sum(1 for t in _tasks if t.status == "Done")
    high_count = sum(1 for t in _tasks if t.priority == "High")
    recent = _tasks[-3:]

    rows = "".join(f"""
        <tr>
          <td><strong>{t.title}</strong></td>
          <td><span class="badge badge-{t.priority.replace(' ', '.')}">{t.priority}</span></td>
          <td><span class="badge badge-{t.status.replace(' ', '.')}">{t.status}</span></td>
          <td>{t.created_at}</td>
        </tr>""" for t in reversed(recent))

    body = f"""
<div class="stat-grid">
  <div class="stat-card"><div class="stat-num">{total}</div><div>Total Tasks</div></div>
  <div class="stat-card"><div class="stat-num">{open_count}</div><div>Open</div></div>
  <div class="stat-card"><div class="stat-num">{done_count}</div><div>Done</div></div>
</div>
<div class="card">
  <h2>Recent Activity</h2>
  <table>
    <tr><th>Title</th><th>Priority</th><th>Status</th><th>Created</th></tr>
    {rows}
  </table>
  <p><a href="/tasks">View all {total} tasks →</a></p>
</div>
<div class="card">
  <h2>Quick Stats</h2>
  <p>High priority tasks: <strong>{high_count}</strong></p>
  <p>Completion rate: <strong>{int(done_count/total*100) if total else 0}%</strong></p>
  <p><a href="/create">+ Create new task</a></p>
</div>"""
    return HTMLResponse(_base_html("Dashboard", "Dashboard", body))


@app.get("/tasks", response_class=HTMLResponse)
async def task_list(
    priority: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1, ge=1),
):
    tasks_page, total = _filter_tasks(priority=priority, status=status, page=page)
    total_pages = max(1, -(-total // PAGE_SIZE))  # ceiling division

    priority_opts = "".join(
        f'<option value="{p}"{" selected" if p == priority else ""}>{p}</option>'
        for p in PRIORITIES
    )
    status_opts = "".join(
        f'<option value="{s}"{" selected" if s == status else ""}>{s}</option>'
        for s in STATUSES
    )

    rows = "".join(f"""
        <tr>
          <td>{t.task_id}</td>
          <td><a href="/tasks/{t.task_id}"><strong>{t.title}</strong></a></td>
          <td><span class="badge badge-{t.priority.replace(' ', '.')}">{t.priority}</span></td>
          <td><span class="badge badge-{t.status.replace(' ', '.')}">{t.status}</span></td>
          <td>{t.created_at}</td>
          <td>
            <form method="POST" action="/tasks/{t.task_id}/complete" style="display:inline">
              <button class="secondary" type="submit">Mark Done</button>
            </form>
          </td>
        </tr>""" for t in tasks_page) or "<tr><td colspan='6'>No tasks found.</td></tr>"

    pages_html = "".join(
        f'<a href="/tasks?priority={priority}&status={status}&page={p}" '
        f'class="{"current" if p == page else ""}">{p}</a>'
        for p in range(1, total_pages + 1)
    )

    body = f"""
<div class="card">
  <h2>Task List <small style="color:#888">({total} tasks)</small></h2>
  <form method="GET" action="/tasks">
    <div class="filter-bar">
      <label>Priority:</label>
      <select name="priority" id="priority-filter">
        <option value="">All</option>
        {priority_opts}
      </select>
      <label>Status:</label>
      <select name="status" id="status-filter">
        <option value="">All</option>
        {status_opts}
      </select>
      <button class="primary" type="submit">Filter</button>
      <a href="/tasks"><button type="button" class="secondary">Reset</button></a>
    </div>
  </form>
  <table>
    <tr><th>ID</th><th>Title</th><th>Priority</th><th>Status</th><th>Created</th><th>Action</th></tr>
    {rows}
  </table>
  <div class="pagination">
    Page: {pages_html}
    <span style="color:#888; font-size:.85rem">({total} total)</span>
  </div>
</div>"""
    return HTMLResponse(_base_html("All Tasks", "All Tasks", body))


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: str):
    task = next((t for t in _tasks if t.task_id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = f"""
<div class="card">
  <h2>{task.title}</h2>
  <p><strong>ID:</strong> {task.task_id}</p>
  <p><strong>Priority:</strong> <span class="badge badge-{task.priority.replace(' ', '.')}">{task.priority}</span></p>
  <p><strong>Status:</strong> <span class="badge badge-{task.status.replace(' ', '.')}">{task.status}</span></p>
  <p><strong>Description:</strong> {task.description or "No description provided."}</p>
  <p><strong>Created:</strong> {task.created_at}</p>
  <hr>
  <form method="POST" action="/tasks/{task.task_id}/complete">
    <button class="primary" type="submit">Mark as Done</button>
  </form>
  <br><a href="/tasks">← Back to Task List</a>
</div>"""
    return HTMLResponse(_base_html(f"Task: {task.title}", "All Tasks", body))


@app.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
async def mark_complete(task_id: str):
    task = next((t for t in _tasks if t.task_id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # BUG-F2-03: Status is NOT updated despite the action succeeding visually.
    # Correct: task.status = "Done"
    # Broken: the status field is left unchanged
    body = f"""
<div class="card">
  <h2 class="success">✓ Task Marked as Done</h2>
  <p>Task <strong>{task.title}</strong> has been marked as complete.</p>
  <p>Current status: <span class="badge badge-{task.status.replace(' ', '.')}" id="task-status">{task.status}</span></p>
  <p><a href="/tasks">← Back to Task List</a></p>
</div>"""
    return HTMLResponse(_base_html("Task Complete", "All Tasks", body))


@app.get("/create", response_class=HTMLResponse)
async def create_task_form(error: str = ""):
    error_html = f'<p class="error">{error}</p>' if error else ""
    priority_opts = "".join(
        f'<option value="{p}">{p}</option>' for p in PRIORITIES
    )
    body = f"""
<div class="card">
  <h2>Create New Task</h2>
  {error_html}
  <form method="POST" action="/create" id="create-task-form">
    <label for="title"><strong>Title *</strong></label>
    <input type="text" id="title" name="title" placeholder="Enter task title" maxlength="100">
    <label for="description"><strong>Description</strong></label>
    <textarea id="description" name="description" rows="3" placeholder="Optional task description"></textarea>
    <label for="priority"><strong>Priority</strong></label>
    <select id="priority" name="priority">
      {priority_opts}
    </select>
    <button class="primary" type="submit" id="submit-task">Create Task</button>
  </form>
</div>"""
    return HTMLResponse(_base_html("Create Task", "Create Task", body))


@app.post("/create", response_class=HTMLResponse)
async def create_task(
    title: str = Form(default=""),
    description: str = Form(default=""),
    priority: str = Form(default="Medium"),
):
    # BUG-F2-01: Empty title is accepted without validation.
    # Correct behavior: if not title.strip(): redirect with error
    # Buggy behavior: empty title task is created silently
    new_task = Task(
        title=title.strip() if title.strip() else "(no title)",
        description=description.strip(),
        priority=priority if priority in PRIORITIES else "Medium",
    )
    _tasks.append(new_task)

    body = f"""
<div class="card">
  <h2 class="success">✓ Task Created Successfully</h2>
  <p>Task <strong>"{new_task.title}"</strong> has been created.</p>
  <p><strong>Priority:</strong> {new_task.priority}</p>
  <p><strong>Task ID:</strong> {new_task.task_id}</p>
  <p>
    <a href="/tasks">View All Tasks</a> |
    <a href="/create">Create Another</a> |
    <a href="/tasks/{new_task.task_id}">View This Task</a>
  </p>
</div>"""
    return HTMLResponse(_base_html("Task Created", "Create Task", body))


@app.get("/about", response_class=HTMLResponse)
async def about():
    body = """
<div class="card">
  <h2>About TaskDash</h2>
  <p>TaskDash is a simple project task management dashboard.</p>
  <p>It is provided as a test fixture for <strong>VerityQA</strong> — an Autonomous AI QA Engineer
     built for the iMocha CODE APEX 3.0 hackathon (problem statement IM-02).</p>
  <h3>Structural features (for QA exploration)</h3>
  <ul>
    <li>Tab-based navigation (Dashboard / All Tasks / Create Task / About)</li>
    <li>Filterable and paginated task list</li>
    <li>Task detail page</li>
    <li>Task creation form with validation</li>
    <li>In-place status update via form submission</li>
  </ul>
  <h3>Planted defects</h3>
  <ul>
    <li><strong>BUG-F2-01</strong>: Empty task title accepted without validation</li>
    <li><strong>BUG-F2-02</strong>: Priority filter silently ignores selected priority</li>
    <li><strong>BUG-F2-03</strong>: Marking task as Done does not update its status</li>
  </ul>
</div>"""
    return HTMLResponse(_base_html("About", "About", body))


@app.get("/api/tasks")
async def api_tasks():
    """JSON API for programmatic access."""
    return JSONResponse([
        {
            "task_id": t.task_id,
            "title": t.title,
            "priority": t.priority,
            "status": t.status,
            "created_at": t.created_at,
        }
        for t in _tasks
    ])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("demo_app.fixture2.main:app", host="127.0.0.1", port=8002, reload=True)
