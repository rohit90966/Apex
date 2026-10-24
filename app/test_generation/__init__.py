"""
VerityQA Test-Case Generation Module.
Consumes the versioned `InvestigationHandoffContract` artifact to produce
evidence-traceable, safety-classified QA test suites.
"""

from app.test_generation.contract_test_generator import (
    ExecutionSafetyClassification,
    TestCategoryType,
    GroundedTestStep,
    ExpectedResultSpec,
    SafetyExecutionMetadata,
    GeneratedContractTestCase,
    TestGenerationCoverageMap,
    ContractTestSuiteArtifact,
    ContractTestValidator,
    ContractTestGenerator,
)
from app.test_generation.playwright_exporter import (
    PlaywrightTestExporter,
)
from app.test_generation.test_runner import (
    PlaywrightContractTestRunner,
    ContractTestExecutionReport,
    TestCaseExecutionResult,
    TestExecutionStatus,
    FailurePhase,
    NonExecutableTestCaseInfo,
)

__all__ = [
    "ExecutionSafetyClassification",
    "TestCategoryType",
    "GroundedTestStep",
    "ExpectedResultSpec",
    "SafetyExecutionMetadata",
    "GeneratedContractTestCase",
    "TestGenerationCoverageMap",
    "ContractTestSuiteArtifact",
    "ContractTestValidator",
    "ContractTestGenerator",
    "PlaywrightTestExporter",
    "PlaywrightContractTestRunner",
    "ContractTestExecutionReport",
    "TestCaseExecutionResult",
    "TestExecutionStatus",
    "FailurePhase",
    "NonExecutableTestCaseInfo",
]
