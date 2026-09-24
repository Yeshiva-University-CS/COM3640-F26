"""
The PipelineQL engine: the standard entry point for every submission.

Run a program with:

    uv run main.py program.ql

You should (must) not edit this file. It handles the parts every interpreter
does the same way: reading the program, loading your grammar, turning a parse
failure into a GrammarError, reporting errors, and choosing an exit status.
You supply the two pieces that are yours to design, both next to this file:

    pipelineql.lark   your grammar
    interpreter.py     your validator and executor

`interpreter.py` must define two functions:

    validate(tree) -> None
        Check the parse tree. Raise ValidationErrors catching every Tier 1
        problem, or, once Tier 1 is clean, every Tier 2 problem. Return
        normally if the program is valid.

    execute(tree) -> None
        Run the validated program by calling pipeline_runtime, wrapping any
        PipelineRuntimeError in an ExecutionError.

While you are still designing the grammar, `--tree` parses a program and
prints the tree without needing `interpreter.py` to exist yet:

    uv run main.py program.ql --tree

Exit status:

    0   the program ran
    1   the program was rejected, or failed while running
    2   a setup problem: bad usage, or a missing or malformed file
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import lark

import pipeline_runtime as rt
from pipeline_errors import (
    ExecutionError,
    GrammarError,
    PipelineQLError,
    ValidationErrors,
)


GRAMMAR_FILE = "pipelineql.lark"
INTERPRETER_MODULE = "interpreter"

EXIT_OK = 0
EXIT_PROGRAM_ERROR = 1
EXIT_SETUP_ERROR = 2


class _SetupError(Exception):
    """
    Something about the submission itself is wrong: a missing file, a grammar
    that won't load, an interpreter module that doesn't fit the contract.
    This is never a complaint about the .ql program being run.
    """


def _read_program(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise _SetupError(f"could not read {path}: {e.strerror or e}") from e


def _load_interpreter(required: bool):
    """
    Import `interpreter.py` from beside this file and check it defines what
    the engine calls. Returns None when it's absent but not required, which
    is what lets --tree work before it has been written.
    """
    try:
        module = importlib.import_module(INTERPRETER_MODULE)
    except ModuleNotFoundError as e:
        # A ModuleNotFoundError naming something else came from inside the
        # student's own module; let it surface with its traceback.
        if e.name != INTERPRETER_MODULE:
            raise
        if not required:
            return None
        raise _SetupError(
            f"no {INTERPRETER_MODULE}.py next to main.py. It must define "
            f"validate(tree) and execute(tree)."
        ) from e

    missing = [
        name
        for name in ("validate", "execute")
        if not callable(getattr(module, name, None))
    ]
    if missing and required:
        raise _SetupError(
            f"{INTERPRETER_MODULE}.py does not define "
            f"{' and '.join(f'{name}(tree)' for name in missing)}"
        )
    return module


def _build_parser(start: str, parser: str) -> lark.Lark:
    path = Path(__file__).with_name(GRAMMAR_FILE)
    if not path.is_file():
        raise _SetupError(
            f"no {GRAMMAR_FILE} next to main.py. Your grammar belongs in that "
            f"file; the engine loads it from disk rather than from a string."
        )
    try:
        return lark.Lark.open(str(path), start=start, parser=parser)
    except lark.exceptions.LarkError as e:
        raise _SetupError(
            f"could not load {GRAMMAR_FILE} with start rule {start!r} and "
            f"parser {parser!r}:\n{e}\n"
            f"(if your top rule has another name, set START_RULE in "
            f"{INTERPRETER_MODULE}.py)"
        ) from e


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="uv run main.py",
        description="Run a PipelineQL program.",
    )
    parser.add_argument(
        "program", type=Path, metavar="PROGRAM.ql", help="the .ql file to run"
    )
    parser.add_argument(
        "--tree",
        action="store_true",
        help="print the parse tree and stop, without validating or running",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        source = _read_program(args.program)
        interpreter = _load_interpreter(required=not args.tree)
        grammar = _build_parser(
            start=getattr(interpreter, "START_RULE", "start"),
            parser=getattr(interpreter, "PARSER", "earley"),
        )
    except _SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SETUP_ERROR

    try:
        try:
            tree = grammar.parse(source)
        except lark.exceptions.LarkError as e:
            raise GrammarError(str(e), line=getattr(e, "line", None)) from e

        if args.tree:
            print(tree.pretty())
            return EXIT_OK

        interpreter.validate(tree)

        try:
            interpreter.execute(tree)
        except rt.PipelineRuntimeError as e:
            # execute() is supposed to wrap these itself. Catching one here
            # means it let a runtime failure through, so wrap it rather than
            # dumping a traceback on the person running the program.
            raise ExecutionError(str(e)) from e

    except ValidationErrors as errors:
        print(f"{len(errors)} validation error(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return EXIT_PROGRAM_ERROR
    except PipelineQLError as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_PROGRAM_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
