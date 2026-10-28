"""The plan a screened export's scripted draft planner writes, as one typed document.

The journey states it and `actions.py` reads it back, so both hold one shape: a private
coordinating project, its export edge, and its tasks, each keeping its own visibility.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from orchestrator.plan_budgets import ModernBudget


class ExportEdge(BaseModel):
    """The one `onepipeline.export_edges` entry carrying the generalized work across."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: str = Field(alias="from")
    to: str
    source_directory: str
    target_directory: str


class TaskSpec(BaseModel):
    """One task of the plan, as the `plan-task` template is answered for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    repository: str
    execution_checkout: str
    clearance: Literal["private", "public"]
    what: str
    why: str
    criteria: list[str]
    deps: list[str] = []
    #: `preserve` keeps the producer's branch on its origin rather than landing it.
    publish: Literal["preserve"] | None = None
    budgets: list[ModernBudget] = []


class PlanSpec(BaseModel):
    """The whole plan: its project, its goal, its plan-level budget answers and its tasks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str
    title: str
    goal: str
    #: The `plan-description` template's answers, recorded verbatim as the plan's budgets.
    budgets: dict[str, JsonValue]
    export_edges: list[ExportEdge]
    #: In dependency order: each task after the tasks it depends on.
    tasks: list[TaskSpec]
