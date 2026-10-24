"""
Benchmark targets: only the inputs a QA engineer would legitimately receive.

Nothing here describes a planted defect. `requirements` are each application's published product
rules (correct and buggy behaviours alike are covered by them); they are what an explicit
specification looks like, not a hint list.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

REPOSITORY_FIXTURE = "REPOSITORY_FIXTURE"
PUBLIC_INDEPENDENT = "PUBLIC_INDEPENDENT"
CONTROLLED_SYNTHETIC = "CONTROLLED_SYNTHETIC"


@dataclass(frozen=True)
class TargetSpec:
    key: str
    name: str
    target_class: str
    description: str
    app_module: Optional[str] = None          # uvicorn "module:app" for local fixtures
    ports: Dict[str, int] = field(default_factory=dict)  # mode -> port
    public_url: Optional[str] = None
    requirements: List[str] = field(default_factory=list)
    credentials: Optional[Dict[str, str]] = None  # authorized demo credentials for this origin only
    expected_port_url: Optional[str] = None   # externally provided target (e.g. TaskFlow)

    def url(self, mode: str) -> str:
        if self.public_url:
            return self.public_url
        if self.expected_port_url:
            return self.expected_port_url
        return f"http://127.0.0.1:{self.ports[mode]}/"


TARGETS: List[TargetSpec] = [
    TargetSpec(
        key="apex_portal",
        name="Apex Demo Portal (fixture 1)",
        target_class=REPOSITORY_FIXTURE,
        description="Portal with sign-in, account registration and catalogue search.",
        app_module="demo_app.main:app",
        ports={"buggy": 8101, "baseline": 8111},
        requirements=[
            "Username is required to sign in",
            "Password is required to sign in",
            "Password must be at least 6 characters",
            "Full name is required for registration",
            "Accepting the Terms of Service is required",
            "Search query is required",
        ],
    ),
    TargetSpec(
        key="taskdash",
        name="TaskDash (fixture 2)",
        target_class=REPOSITORY_FIXTURE,
        description="Project task management dashboard with task list, filters and task creation.",
        app_module="demo_app.fixture2.main:app",
        ports={"buggy": 8102, "baseline": 8112},
        requirements=[
            "Task title is required",
            "Task title must be at most 100 characters",
        ],
    ),
    TargetSpec(
        key="benchmark_shop",
        name="Intelligence benchmark shop (controlled synthetic e-commerce)",
        target_class=CONTROLLED_SYNTHETIC,
        description="Small online shop with login, product catalogue, sorting, cart and checkout.",
        app_module="demo_app.benchmark_app.main:app",
        ports={"buggy": 8103, "baseline": 8113},
        requirements=["First name is required at checkout", "Postal code is required at checkout"],
        credentials={"username": "benchmark_user", "password": "secret_benchmark"},
    ),
    TargetSpec(
        key="taskflow",
        name="TaskFlow (fixture 3)",
        target_class=REPOSITORY_FIXTURE,
        description="Task and project management SaaS.",
        expected_port_url="http://127.0.0.1:8003/",
    ),
    TargetSpec(
        key="saucedemo",
        name="SauceDemo (public demo)",
        target_class=PUBLIC_INDEPENDENT,
        description="E-commerce demo shop with login, inventory, sorting, cart and checkout.",
        public_url="https://www.saucedemo.com/",
        requirements=["Username is required", "Password is required"],
    ),
]


def get_target(key: str) -> TargetSpec:
    return next(t for t in TARGETS if t.key == key)
