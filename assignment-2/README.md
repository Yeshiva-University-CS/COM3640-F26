### COM-3640 Programming Languages
### Grammars and Parsing

# PipelineQL: A Data Pipeline Scripting Language

## Background and Motivation

We've discussed regular expressions and context-free grammars, and how a parser turns program text into a structure your code can act on. This assignment asks you to design and build a small domain-specific language from scratch: **PipelineQL**, for describing data-integration pipelines. For example, reading tabular data from a file, filtering and reshaping it, joining it with other data, summarizing it, and writing results back out.

## Getting Started

Start with [pipelineql_semantics.md](pipelineql_semantics.md), which has the assignment language specifications. Read also [a-note-on-names.md](a-note-on-names.md) before you start designing your grammar.

I have supplied the runtime (`pipeline_runtime.py`), the exception types your interpreter raises (`pipeline_errors.py`), and the runtime's test suite, to make sure it is working on your computer. 

For the assignment, you write the grammar (`pipelineql.lark`) and the interpreter (`interpreter.py`).

## Install uv (skip if you have it)

`uv --version` tells you whether it is already installed. If not:

```
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Other options, including `pip install uv`, are at <https://docs.astral.sh/uv/getting-started/installation/>.

## Set up and verify

```
uv sync
uv run pytest test_pipeline_runtime.py -v
```

`uv sync` creates the virtual environment and installs `duckdb`, `openpyxl`, `lark`, and `pytest`. Every test should pass before you build anything on top of the runtime.

## Run your own programs

`main.py` is the supplied driver. You should (must) not edit it. It reads the program, loads your grammar, reports errors, and sets the exit status. You write the two files next to it that it will need. The exact names are required:

- `pipelineql.lark`: your grammar.
- `interpreter.py`: your validator and executor.

PipelineQL programs are plain text files with a `.ql` extension. Run one with:

```
uv run main.py program.ql
```

While you are still designing the grammar, parse a program and print its tree. This works even before `interpreter.py` exists:

```
uv run main.py program.ql --tree
```

The driver exits `0` if the program ran, `1` if the program was rejected or failed, and `2` if something about your submission is wrong, such as a missing or malformed file.

### `interpreter.py`

`main.py` calls `validate(tree)`, and then, if it returns without raising and exception, `execute(tree)`. See the skeleton provided.

## Submission

Check your work into your Git repo in the PL-COM3640 branch, with the following directory structure:

```
/assignment-2
```

You must submit your two files (`pipeline.lark` and `interpreter.py`) in the above directory. You are free to check in the driver and other `py` files that were provided for you so that you have a full working program; however, I will be testing by copying over your two submission files to my own test harness location.

Do **not** submit compiled binaries, `__pycache__`, `.pytest_cache/`, `.venv/`, or other generated build artifacts.

*I will be providing test data files and pipeline operations for you to test with.*
