from __future__ import annotations


class NoopWorkflowRunner:
    """P1-P3 placeholder runner to make non-execution explicit."""

    def execute(self, *_args, **_kwargs) -> None:
        raise NotImplementedError("Workflow execution is introduced in P5 Runner.")
