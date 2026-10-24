"""
Durable storage for investigation runs.

Layout under the configured root (settings.verityqa_runs_dir):
    <root>/<run_id>/run.json         lifecycle record (RunRecord)
    <root>/<run_id>/report.json      completed investigation report payload
    <root>/<run_id>/test_cases.json  exactly the test cases generated for that report

Guarantees:
- Run IDs are validated against RUN_ID_PATTERN and resolved strictly inside the root,
  so no client-supplied value can select an arbitrary filesystem path.
- Every write is atomic (temp file in the same directory + os.replace).
- Everything persisted passes through redact_secrets.
- A completed status is written only after its report and test cases are on disk.
"""

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from app.runs.redaction import redact_secrets

logger = logging.getLogger(__name__)

RUN_ID_PATTERN = re.compile(r"^inv-[0-9a-f]{8,32}$")
EXECUTION_ID_PATTERN = re.compile(r"^exec-[0-9a-f]{8,32}$")

RUN_FILE = "run.json"
REPORT_FILE = "report.json"
TEST_CASES_FILE = "test_cases.json"
EXECUTIONS_DIR = "executions"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReportOutcome(str, Enum):
    """
    Whether a completed run's investigation actually succeeded. Distinct from RunStatus:
    a run can finish (RunStatus.COMPLETED) with a report that is blocked or failed.
    """
    SUCCEEDED = "succeeded"
    LIMITED = "limited"
    UNSUCCESSFUL = "unsuccessful"
    UNKNOWN = "unknown"


# InvestigationStatus value -> outcome. Mirrored in app/static/src/runDisplay.mjs (REPORT_OUTCOMES).
REPORT_OUTCOMES: Dict[str, ReportOutcome] = {
    "completed": ReportOutcome.SUCCEEDED,
    "partial": ReportOutcome.LIMITED,
    "timed_out": ReportOutcome.LIMITED,
    "budget_exhausted": ReportOutcome.LIMITED,
    "blocked": ReportOutcome.UNSUCCESSFUL,
    "error": ReportOutcome.UNSUCCESSFUL,
    "failed": ReportOutcome.UNSUCCESSFUL,
    "cancelled": ReportOutcome.UNSUCCESSFUL,
    "unsafe_stopped": ReportOutcome.UNSUCCESSFUL,
}


def report_outcome_for(report_status: Optional[str]) -> ReportOutcome:
    if not report_status:
        return ReportOutcome.UNKNOWN
    return REPORT_OUTCOMES.get(str(report_status).lower(), ReportOutcome.UNKNOWN)


class RunRecord(BaseModel):
    run_id: str
    target_url: str
    description: Optional[str] = None
    requirements: List[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.PENDING
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    stopping_reason: Optional[str] = None
    error: Optional[str] = None
    test_case_count: int = 0
    # Set on completion from the report's own status (InvestigationStatus value), e.g. "blocked".
    report_status: Optional[str] = None
    report_outcome: Optional[ReportOutcome] = None
    # Downstream stages, tracked separately from the investigation lifecycle.
    test_generation_status: Optional[str] = None  # "completed" | "failed"
    test_generation_error: Optional[str] = None
    latest_execution_id: Optional[str] = None
    execution_count: int = 0


class RunListResponse(BaseModel):
    runs: List[RunRecord] = Field(default_factory=list)


class RunDetailResponse(BaseModel):
    run: RunRecord
    report: Optional[Dict[str, Any]] = None
    test_cases: List[Dict[str, Any]] = Field(default_factory=list)
    # The most recent persisted ExecuteSuiteResponse for this run's test cases, if any.
    latest_execution: Optional[Dict[str, Any]] = None


class InvalidRunId(ValueError):
    """Raised when a run ID does not match the allowed format."""


class RunNotFound(KeyError):
    """Raised when no persisted run exists for a valid run ID."""


def new_run_id() -> str:
    return f"inv-{uuid.uuid4().hex[:12]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_requirements(requirements: Any) -> List[str]:
    """Accepts a list of strings or newline-separated text and returns non-empty lines."""
    if not requirements:
        return []
    if isinstance(requirements, str):
        items = requirements.split("\n")
    else:
        items = [str(r) for r in requirements]
    return [r.strip() for r in items if r and r.strip()]


class RunStore:
    """File-backed store; one directory per run."""

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self._lock = threading.Lock()

    # --- Paths & IO ---

    def _run_dir(self, run_id: Any) -> Path:
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            raise InvalidRunId("Invalid run ID format")
        path = (self.root / run_id).resolve()
        if path.parent != self.root:
            raise InvalidRunId("Invalid run ID format")
        return path

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _read_json(path: Path) -> Any:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # --- Lifecycle ---

    def create(
        self,
        target_url: str,
        description: Optional[str] = None,
        requirements: Any = None,
        secrets: Optional[Iterable[str]] = None,
    ) -> RunRecord:
        record = RunRecord(
            run_id=new_run_id(),
            target_url=target_url,
            description=description or None,
            requirements=normalize_requirements(requirements),
            status=RunStatus.PENDING,
            created_at=_utc_now(),
        )
        with self._lock:
            self._save_record(record, secrets)
        return record

    def mark_running(self, run_id: str) -> RunRecord:
        return self._update(run_id, None, status=RunStatus.RUNNING, started_at=_utc_now())

    def complete(
        self,
        run_id: str,
        report: Dict[str, Any],
        test_cases: List[Dict[str, Any]],
        stopping_reason: Optional[str] = None,
        secrets: Optional[Iterable[str]] = None,
        report_status: Optional[str] = None,
        test_generation_error: Optional[str] = None,
    ) -> RunRecord:
        run_dir = self._run_dir(run_id)
        with self._lock:
            self._write_json(run_dir / REPORT_FILE, redact_secrets(report, secrets))
            self._write_json(run_dir / TEST_CASES_FILE, redact_secrets(list(test_cases), secrets))
            return self._update_locked(
                run_id,
                secrets,
                status=RunStatus.COMPLETED,
                finished_at=_utc_now(),
                stopping_reason=stopping_reason,
                test_case_count=len(test_cases),
                error=None,
                report_status=report_status,
                report_outcome=report_outcome_for(report_status),
                test_generation_status="failed" if test_generation_error else "completed",
                test_generation_error=test_generation_error,
            )

    def save_execution(
        self,
        run_id: str,
        execution: Dict[str, Any],
        secrets: Optional[Iterable[str]] = None,
    ) -> RunRecord:
        """Persists one ExecuteSuiteResponse for this run and makes it the run's latest execution."""
        execution_id = execution.get("execution_id")
        if not isinstance(execution_id, str) or not EXECUTION_ID_PATTERN.fullmatch(execution_id):
            raise ValueError("Invalid execution ID format")
        run_dir = self._run_dir(run_id)
        with self._lock:
            record = self.get_record(run_id)
            self._write_json(run_dir / EXECUTIONS_DIR / f"{execution_id}.json", redact_secrets(execution, secrets))
            return self._update_locked(
                run_id,
                secrets,
                latest_execution_id=execution_id,
                execution_count=record.execution_count + 1,
            )

    def fail(self, run_id: str, error: str, secrets: Optional[Iterable[str]] = None) -> RunRecord:
        return self._update(run_id, secrets, status=RunStatus.FAILED, finished_at=_utc_now(), error=error)

    def cancel(self, run_id: str, reason: str = "Cancelled", secrets: Optional[Iterable[str]] = None) -> RunRecord:
        return self._update(run_id, secrets, status=RunStatus.CANCELLED, finished_at=_utc_now(), error=reason)

    def recover_interrupted_runs(self) -> int:
        """Marks runs left pending/running by a previous process as failed so they never appear live forever."""
        recovered = 0
        for record in self.list_runs():
            if record.status in (RunStatus.PENDING, RunStatus.RUNNING):
                self.fail(record.run_id, "Server restarted before the investigation finished")
                recovered += 1
        return recovered

    def _update(self, run_id: str, secrets: Optional[Iterable[str]], **changes: Any) -> RunRecord:
        with self._lock:
            return self._update_locked(run_id, secrets, **changes)

    def _update_locked(self, run_id: str, secrets: Optional[Iterable[str]], **changes: Any) -> RunRecord:
        record = self.get_record(run_id)
        updated = record.model_copy(update=changes)
        self._save_record(updated, secrets)
        return updated

    def _save_record(self, record: RunRecord, secrets: Optional[Iterable[str]]) -> None:
        data = redact_secrets(record.model_dump(mode="json"), secrets)
        self._write_json(self._run_dir(record.run_id) / RUN_FILE, data)

    # --- Retrieval ---

    def get_record(self, run_id: str) -> RunRecord:
        path = self._run_dir(run_id) / RUN_FILE
        if not path.is_file():
            raise RunNotFound(run_id)
        return RunRecord(**self._read_json(path))

    def get(self, run_id: str) -> RunDetailResponse:
        record = self.get_record(run_id)
        run_dir = self._run_dir(run_id)
        report_path = run_dir / REPORT_FILE
        cases_path = run_dir / TEST_CASES_FILE
        report = self._read_json(report_path) if report_path.is_file() else None
        test_cases = self._read_json(cases_path) if cases_path.is_file() else []
        latest_execution = None
        if record.latest_execution_id and EXECUTION_ID_PATTERN.fullmatch(record.latest_execution_id):
            execution_path = run_dir / EXECUTIONS_DIR / f"{record.latest_execution_id}.json"
            if execution_path.is_file():
                latest_execution = self._read_json(execution_path)
        return RunDetailResponse(run=record, report=report, test_cases=test_cases, latest_execution=latest_execution)

    def list_runs(self) -> List[RunRecord]:
        if not self.root.is_dir():
            return []
        records: List[RunRecord] = []
        for entry in self.root.iterdir():
            if not entry.is_dir() or not RUN_ID_PATTERN.fullmatch(entry.name):
                continue
            try:
                records.append(self.get_record(entry.name))
            except Exception as e:
                logger.warning("[RunStore] Skipping unreadable run %s: %s", entry.name, e)
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records
