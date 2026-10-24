"""
VerityQA Benchmark — Planted-Bug Manifest and Evaluation Runner

Compares the autonomous agent's discovered BugReports against the known
planted defects in the local demo app, computing:
  - True Positives  (detected planted bugs)
  - False Positives (reported bugs not in manifest)
  - Missed Bugs     (planted bugs not detected)
  - Precision, Recall, F1-Score
"""

from typing import List, Dict, Optional
from app.schemas import BenchmarkManifestItem, BenchmarkRunSummary, BugReport

# ── Ground-Truth Manifest ──────────────────────────────────────────────────────
PLANTED_BUG_MANIFEST: List[BenchmarkManifestItem] = [
    BenchmarkManifestItem(
        bug_id="BUG-01",
        category="Authentication",
        route="POST /login",
        title="Password length validation bypass",
        description="Server accepts passwords of any length, even a single character.",
        trigger_condition="Submit login with username='admin@apex.com' and password='a' (length=1)",
        expected_behavior="Server rejects password shorter than 6 characters with a validation error message.",
        actual_buggy_behavior="Login proceeds and returns 'Invalid credentials' — no minimum-length rejection.",
    ),
    BenchmarkManifestItem(
        bug_id="BUG-02",
        category="Registration",
        route="POST /register",
        title="Terms of Service acceptance bypass",
        description="Registration succeeds without the terms checkbox having been checked.",
        trigger_condition="Submit /register POST without the 'terms' form field included",
        expected_behavior="Server rejects registration with 'You must accept the Terms of Service'.",
        actual_buggy_behavior="Registration succeeds, creating an account without enforcing terms.",
    ),
    BenchmarkManifestItem(
        bug_id="BUG-03",
        category="Registration",
        route="POST /register",
        title="Password confirmation mismatch bypass",
        description="confirm_password is ignored; mismatched passwords are accepted.",
        trigger_condition="Submit /register POST with password='Secret123!' and confirm_password='Different999!'",
        expected_behavior="Server rejects with 'Passwords do not match' validation error.",
        actual_buggy_behavior="Registration succeeds despite mismatched passwords.",
    ),
    BenchmarkManifestItem(
        bug_id="BUG-04",
        category="Search",
        route="GET /search",
        title="Empty search query accepted without validation",
        description="Blank search query returns a generic 'no results' response instead of a validation error.",
        trigger_condition="Submit GET /search?q= (empty string)",
        expected_behavior="Application returns a clear validation message: 'Please enter a search term'.",
        actual_buggy_behavior="Returns 'No results found for \"\"' with no guidance for the user.",
    ),
]

MANIFEST_BY_ID: Dict[str, BenchmarkManifestItem] = {
    item.bug_id: item for item in PLANTED_BUG_MANIFEST
}


class BenchmarkEvaluator:
    """
    Evaluates the set of BugReports produced by the autonomous agent
    against the ground-truth planted-bug manifest.
    """

    def evaluate(self, reported_bugs: List[BugReport]) -> BenchmarkRunSummary:
        from app.schemas import VerificationStatus

        detected_ids: List[str] = []
        false_positive_ids: List[str] = []
        tp_reports: List[BugReport] = []

        for bug in reported_bugs:
            # Exclude oracle defects and environmental noise from benchmark bug metrics
            if getattr(bug, "verification_status", None) == VerificationStatus.TEST_ORACLE_DEFECT:
                continue

            matched_id = self._match_manifest_bug(bug)
            if matched_id:
                if matched_id not in detected_ids:
                    detected_ids.append(matched_id)
                tp_reports.append(bug)
            else:
                false_positive_ids.append(bug.bug_id)

        all_planted_ids = [item.bug_id for item in PLANTED_BUG_MANIFEST]
        missed_ids = [bid for bid in all_planted_ids if bid not in detected_ids]

        total_planted = len(PLANTED_BUG_MANIFEST)
        tp = len(detected_ids)
        fp = len(false_positive_ids)
        fn = len(missed_ids)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        return BenchmarkRunSummary(
            benchmark_name="VerityQA Planted-Bug Benchmark v1.0",
            total_planted_bugs=total_planted,
            true_positives_detected=tp,
            false_positives=fp,
            missed_bugs=fn,
            precision=round(precision, 3),
            recall=round(recall, 3),
            f1_score=round(f1, 3),
            detected_bug_ids=detected_ids,
            missed_bug_ids=missed_ids,
            detailed_reports=tp_reports,
        )

    def _matches_planted_bug(self, bug: BugReport) -> bool:
        return self._match_manifest_bug(bug) is not None

    def _match_manifest_bug(self, bug: BugReport) -> Optional[str]:
        """
        Determines which planted bug (BUG-01..04) matches the reported bug, if any.
        Uses route context, test ID, and semantic triggers.
        """
        from app.schemas import VerificationStatus
        if getattr(bug, "verification_status", None) == VerificationStatus.TEST_ORACLE_DEFECT:
            return None

        combined = " ".join([
            bug.title or "",
            bug.summary or "",
            bug.actual_behavior or "",
            bug.suspected_cause or "",
            bug.target_url or "",
            bug.test_id or "",
        ]).lower()

        # BUG-01: Login password length validation bypass
        if ("login" in combined or "/login" in (bug.target_url or "")) and any(
            k in combined for k in ["length", "short", "minlength", "at least", "character", "password policy", "single character"]
        ):
            return "BUG-01"

        # BUG-02: Terms of service acceptance bypass
        if ("register" in combined or "registration" in combined or "/register" in (bug.target_url or "")) and any(
            k in combined for k in ["term", "tos", "agreement", "unaccepted", "checkbox"]
        ):
            return "BUG-02"

        # BUG-03: Password mismatch bypass
        if ("register" in combined or "registration" in combined or "/register" in (bug.target_url or "")) and any(
            k in combined for k in ["mismatch", "confirm", "differing", "different password", "match", "passwords do not match"]
        ):
            return "BUG-03"

        # BUG-04: Empty search query accepted
        if ("search" in combined or "/search" in (bug.target_url or "")) and any(
            k in combined for k in ["empty", "blank", "whitespace", "no results found for", "please enter", "catalog search"]
        ):
            return "BUG-04"

        # Fallback keyword matching against manifest items
        for item in PLANTED_BUG_MANIFEST:
            keywords = self._extract_keywords(item)
            hits = sum(1 for kw in keywords if kw in combined)
            if hits >= 3:
                return item.bug_id

        return None

    def _nearest_manifest_id(self, bug: BugReport) -> str:
        matched = self._match_manifest_bug(bug)
        return matched or PLANTED_BUG_MANIFEST[0].bug_id

    def _extract_keywords(self, item: BenchmarkManifestItem) -> List[str]:
        """Extracts signal keywords from a manifest item for semantic matching."""
        base = f"{item.title} {item.description} {item.actual_buggy_behavior}".lower()
        import re
        words = re.findall(r'\b[a-z]{3,}\b', base)
        stop = {"the", "and", "that", "with", "for", "this", "are", "not", "been", "from"}
        return [w for w in words if w not in stop][:12]


# ── Fixture 2 — TaskDash Planted Bug Manifest ─────────────────────────────────

FIXTURE2_BUG_MANIFEST: List[BenchmarkManifestItem] = [
    BenchmarkManifestItem(
        bug_id="BUG-F2-01",
        category="Input Validation",
        route="POST /create",
        title="Empty task title accepted without validation",
        description="The task creation form accepts an empty title field and creates a task titled '(no title)'.",
        trigger_condition="Submit POST /create with title='' (empty string)",
        expected_behavior="Application rejects the form with a validation error: 'Title is required'.",
        actual_buggy_behavior="Task is created with title '(no title)' and a success page is shown.",
    ),
    BenchmarkManifestItem(
        bug_id="BUG-F2-02",
        category="Filtering",
        route="GET /tasks",
        title="Priority filter silently ignores selected priority",
        description="When a priority is selected in the filter, the task list returns ALL tasks regardless of priority.",
        trigger_condition="GET /tasks?priority=High — expects only High priority tasks",
        expected_behavior="Only tasks matching the selected priority are shown in the task list.",
        actual_buggy_behavior="All tasks are shown regardless of the priority filter selection.",
    ),
    BenchmarkManifestItem(
        bug_id="BUG-F2-03",
        category="State Update",
        route="POST /tasks/{task_id}/complete",
        title="Marking a task complete does not update its status",
        description="After clicking 'Mark Done', the task status badge still shows the original status (e.g. 'Open') instead of 'Done'.",
        trigger_condition="POST /tasks/{id}/complete on a task with status 'Open'",
        expected_behavior="Task status is updated to 'Done' in the data store and reflected in the list view.",
        actual_buggy_behavior="Success page is shown, but the task status remains unchanged when the task list is revisited.",
    ),
]

FIXTURE2_MANIFEST_BY_ID: Dict[str, BenchmarkManifestItem] = {
    item.bug_id: item for item in FIXTURE2_BUG_MANIFEST
}


class Fixture2BenchmarkEvaluator:
    """
    Evaluates BugReports produced against the TaskDash fixture (Fixture 2)
    against the three planted defects (BUG-F2-01..03).
    """

    def evaluate(self, reported_bugs: List[BugReport]) -> BenchmarkRunSummary:
        from app.schemas import VerificationStatus

        detected_ids: List[str] = []
        false_positive_ids: List[str] = []
        tp_reports: List[BugReport] = []

        for bug in reported_bugs:
            if getattr(bug, "verification_status", None) == VerificationStatus.TEST_ORACLE_DEFECT:
                continue
            matched_id = self._match_manifest_bug(bug)
            if matched_id:
                if matched_id not in detected_ids:
                    detected_ids.append(matched_id)
                tp_reports.append(bug)
            else:
                false_positive_ids.append(bug.bug_id)

        all_ids = [item.bug_id for item in FIXTURE2_BUG_MANIFEST]
        missed_ids = [bid for bid in all_ids if bid not in detected_ids]

        tp = len(detected_ids)
        fp = len(false_positive_ids)
        fn = len(missed_ids)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        return BenchmarkRunSummary(
            benchmark_name="VerityQA Fixture2 TaskDash Benchmark v1.0",
            total_planted_bugs=len(FIXTURE2_BUG_MANIFEST),
            true_positives_detected=tp,
            false_positives=fp,
            missed_bugs=fn,
            precision=round(precision, 3),
            recall=round(recall, 3),
            f1_score=round(f1, 3),
            detected_bug_ids=detected_ids,
            missed_bug_ids=missed_ids,
            detailed_reports=tp_reports,
        )

    def _match_manifest_bug(self, bug: BugReport) -> Optional[str]:
        from app.schemas import VerificationStatus
        if getattr(bug, "verification_status", None) == VerificationStatus.TEST_ORACLE_DEFECT:
            return None

        combined = " ".join([
            bug.title or "",
            bug.summary or "",
            bug.actual_behavior or "",
            bug.suspected_cause or "",
            bug.target_url or "",
            bug.test_id or "",
        ]).lower()

        # BUG-F2-01: Empty title accepted
        if any(k in combined for k in ["empty title", "no title", "blank title", "title required",
                                        "title is required", "missing title", "empty task"]):
            return "BUG-F2-01"

        # BUG-F2-02: Priority filter broken
        if any(k in combined for k in ["priority filter", "filter ignored", "filter broken",
                                        "filter silently", "all tasks regardless", "priority ignored",
                                        "filter does not", "filter not working"]):
            return "BUG-F2-02"
        if "filter" in combined and "priority" in combined and any(
            k in combined for k in ["all tasks", "not filtered", "shows all", "ignores"]
        ):
            return "BUG-F2-02"

        # BUG-F2-03: Status not updated after mark complete
        if any(k in combined for k in ["status not updated", "mark done", "mark complete",
                                        "complete not", "completion status", "status unchanged",
                                        "still open", "status remains", "not marked", "mark as done"]):
            return "BUG-F2-03"

        # Fallback keyword matching
        for item in FIXTURE2_BUG_MANIFEST:
            import re
            base = f"{item.title} {item.description} {item.actual_buggy_behavior}".lower()
            words = re.findall(r'\b[a-z]{4,}\b', base)
            stop = {"that", "with", "this", "from", "when", "after", "task"}
            keywords = [w for w in words if w not in stop][:10]
            hits = sum(1 for kw in keywords if kw in combined)
            if hits >= 3:
                return item.bug_id

        return None
