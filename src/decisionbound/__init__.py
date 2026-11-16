"""DecisionBound: evidence-grounded decisions for portable engineering agents.

The public API intentionally exposes *ports* and *data contracts*. Downstream
applications can subclass an abstract component or supply a compatible custom
implementation without modifying the core package.

This alpha defaults to local, read-only work. Provider calls, writable tools,
remote synchronization and unattended coding are not enabled by importing it.
"""

from .models import Decision, Evidence, Project, TransferResult, ContextItem, ContextCapsule
from .ports import DecisionRepository, TransferPolicy, ContextCompiler, AgentWorker
from .transfer import ConservativeTransferPolicy
from .context import SelectiveContextCompiler
from .storage import SQLiteRepository
from .workflows import (WorkflowTask, WorkflowSpec, WorkflowJournal, WorkflowSupervisor,
                        WorkflowWorker, TaskAssignment, TaskHandoff, WorkerReceipt,
                        IndependentTaskVerifier, PreparationWorker, ScriptedWorkflowWorker,
                        ModelInspectionWorker, CodingRepairWorker)

__version__ = "0.6.0a1"
__all__ = [
    "Decision", "Evidence", "Project", "TransferResult", "ContextItem", "ContextCapsule",
    "DecisionRepository", "TransferPolicy", "ContextCompiler", "AgentWorker",
    "ConservativeTransferPolicy", "SelectiveContextCompiler", "SQLiteRepository",
    "WorkflowTask", "WorkflowSpec", "WorkflowJournal", "WorkflowSupervisor",
    "WorkflowWorker", "TaskAssignment", "TaskHandoff", "WorkerReceipt",
    "IndependentTaskVerifier", "PreparationWorker", "ScriptedWorkflowWorker",
    "ModelInspectionWorker", "CodingRepairWorker",
]
