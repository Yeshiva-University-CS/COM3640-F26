# Your PipelineQL validator and executor. 
# main.py calls validate, then execute

import pipeline_runtime as rt
from pipeline_errors import (
    ExecutionError,
    SchemaError,
    StructuralError,
    ValidationErrors,
)

# Uncomment and edit if your top rule isn't called "start", 
# or you require LALR:
# START_RULE = "start"
# PARSER = "earley"


def validate(tree):
    """Raise ValidationErrors to log every problem found; return if valid."""
    errors: list[StructuralError | SchemaError] = []

    # Tier 1: walk the tree and append a StructuralError for each problem.

    if not errors:
        # Tier 2: Append a SchemaError for each problem.
        pass

    if errors:
        raise ValidationErrors(errors)


def execute(tree):
    """Run the validated program, wrapping any runtime failure."""
    try:
        pass
    except rt.PipelineRuntimeError as e:
        raise ExecutionError(str(e)) from e
