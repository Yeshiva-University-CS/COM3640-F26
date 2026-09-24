"""
pipeline_errors: the exception hierarchy a PipelineQL interpreter raises.

A student's interpreter passes program text through three distinct stages,
each of which can fail for a different reason and at a different point:

    1. Parsing:    Lark turns program text into a parse tree / AST.
    2. Validation: the AST is checked for problems, split into two tiers:
                       Tier 1 (structural):       from the AST alone
                       Tier 2 (schema-dependent): needs a source's actual
                                                    columns/types
    3. Execution:  the validated program actually runs against
                     pipeline_runtime.py, which raises PipelineRuntimeError
                     for any failure it hits (Tier 3).

Raising a distinct exception type per stage, instead of letting Lark's own
exceptions and pipeline_runtime.PipelineRuntimeError leak straight through,
means calling code (a CLI entry point, a test, a grading harness) can catch
`PipelineQLError` to handle any of them generically, or catch one specific
subclass to handle just that stage.

Typical usage:

    from pipeline_errors import GrammarError, StructuralError, SchemaError, \\
        ExecutionError, ValidationErrors
    import pipeline_runtime as rt

    # 1. Parsing
    try:
        tree = parser.parse(program_text)
    except lark.exceptions.LarkError as e:
        raise GrammarError(str(e), line=getattr(e, "line", None)) from e

    # 2. Validation: collect everything, don't stop at the first problem
    errors = []
    for check in structural_checks:
        try:
            check(ast)
        except StructuralError as e:
            errors.append(e)
    if not errors:                   # Tier 2 runs only once Tier 1 is clean
        for check in schema_checks:
            try:
                check(ast)
            except SchemaError as e:
                errors.append(e)
    if errors:
        raise ValidationErrors(errors)

    # 3. Execution
    try:
        run(ast)
    except rt.PipelineRuntimeError as e:
        raise ExecutionError(str(e), line=current_line) from e
"""

from __future__ import annotations

import enum


__all__ = [
    "Tier",
    "PipelineQLError",
    "GrammarError",
    "SemanticError",
    "StructuralError",
    "SchemaError",
    "ExecutionError",
    "ValidationErrors",
]


class Tier(enum.Enum):
    """Which checking stage an error belongs to."""

    GRAMMAR = "grammar"      # program text doesn't parse at all
    STRUCTURAL = "tier1"     # checkable from the AST alone
    SCHEMA = "tier2"         # needs a source's actual columns/types
    RUNTIME = "tier3"        # only discoverable while executing


class PipelineQLError(Exception):
    """
    Base class for every error a PipelineQL interpreter raises. Catch this
    to handle any of them without caring which stage produced it.

    Don't instantiate this class directly; raise one of the concrete
    subclasses below, each of which sets `tier` at the class level so
    generic error-handling code can inspect *what kind* of failure it
    caught without an `isinstance` chain.
    """

    tier: Tier | None = None

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line

    def __str__(self) -> str:
        if self.line is not None:
            return f"line {self.line}: {self.message}"
        return self.message


class GrammarError(PipelineQLError):
    """
    The program text doesn't parse at all; Lark rejected it before any AST
    could be built (a stray token, unbalanced braces, an unrecognized
    statement shape, ...).

    Wrap whatever Lark raised (`UnexpectedToken`, `UnexpectedCharacters`,
    `UnexpectedEOF`, ...) in one of these rather than letting Lark's own
    exception types leak past the parser boundary. Chain the original with
    `raise GrammarError(...) from original_lark_exception` so nothing about
    the underlying cause is lost.
    """

    tier = Tier.GRAMMAR


class SemanticError(PipelineQLError):
    """
    Common base for both AST-validation tiers, structural (Tier 1) and
    schema-dependent (Tier 2). Catch this to handle "the program parsed,
    but isn't valid" without caring which of the two sub-tiers caught it.

    Not meant to be raised directly; raise `StructuralError` or
    `SchemaError` instead, whichever actually applies.
    """


class StructuralError(SemanticError):
    """
    Tier 1: checkable from the AST alone, before touching any file. An
    undeclared name, a name declared twice, a malformed column list, an
    output path colliding with a source path, a cast to an unrecognized
    type name, and so on.
    """

    tier = Tier.STRUCTURAL


class SchemaError(SemanticError):
    """
    Tier 2: only checkable by inspecting a source's actual columns or the
    runtime's type system. A renamed/selected/grouped field that doesn't
    actually exist on its source, a sheet name the workbook doesn't
    contain, a cast the runtime's type system rejects, and so on.

    Check these using pipeline_runtime.py's schema-only functions
    (`read_csv_schema`, `read_json_schema`, `read_excel_workbook_schema`)
    and `can_cast`; they're cheap specifically so this tier can run before
    committing to execute anything.
    """

    tier = Tier.SCHEMA


class ExecutionError(PipelineQLError):
    """
    Tier 3: only discoverable while actually running the program.

    Wrap a caught `pipeline_runtime.PipelineRuntimeError` (or any other
    failure surfaced while executing) in one of these. Chain it with
    `raise ExecutionError(...) from original_error` so the underlying
    DuckDB/runtime failure stays visible instead of being swallowed.
    """

    tier = Tier.RUNTIME


class ValidationErrors(PipelineQLError):
    """
    Carries every `SemanticError` a single validation pass found, not just
    the first: every Tier 1 problem, or, once Tier 1 is clean, every Tier 2
    problem. The baseline requirement is that a validator reports *all* of
    them: run every check in the tier, collect every failure into a list
    instead of raising on the first one, and raise exactly one
    `ValidationErrors` at the end wrapping the whole list.

    Iterate over an instance to get the individual errors back:

        try:
            validate(ast)
        except ValidationErrors as errs:
            for e in errs:
                print(e)
    """

    def __init__(self, errors: list[SemanticError]) -> None:
        if not errors:
            raise ValueError("ValidationErrors requires at least one error")
        self.errors: list[SemanticError] = list(errors)
        summary = "; ".join(str(e) for e in self.errors)
        super().__init__(f"{len(self.errors)} validation error(s): {summary}")

    def __iter__(self):
        return iter(self.errors)

    def __len__(self) -> int:
        return len(self.errors)
