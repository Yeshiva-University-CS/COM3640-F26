# PipelineQL Semantics

## Introduction

Unlike the grammar exercises, you are not given a grammar, or even a concrete syntax, to start from. This document describes only what your language must be **able to do**; every keyword, symbol, and statement shape is yours to invent (except for the type names and aggregation operations). Designing a clear, unambiguous notation is the assignment.

## The Runtime Library

You will not write a CSV/JSON/Excel parser, a join algorithm, or a type-casting system yourself. A supplied library, `pipeline_runtime.py`, does all of that. Your job is to:

1. Design a grammar, using Lark, for a language that can express every requirement below.
2. Parse a program into a tree.
3. Walk that tree to validate the program, catching as many problems as possible before running anything. If it's valid, execute it by calling the right `pipeline_runtime.py` functions in the right order.

Read `pipeline_runtime.py` directly; its docstrings are your API reference. The pieces you'll use:

- `read_csv(path)`, `read_json(path)`, `read_excel_workbook(path)` load data. CSV and JSON each give you one table; Excel gives you a **set** of tables, one per sheet.
- `Table.filter(condition_sql)`, `Table.map(mappings)`, `Table.aggregate(group_by, aggregates)`, `Table.select(columns)` transform a table. Every one of these returns a **new** table; none of them modify the table you called it on.
- `merge(bindings)` joins two or more tables, given the specific key column to use on each one.
- `Table.write_csv(path)`, `Table.write_json(path)` write one table; `write_excel_workbook(sheets, path)` writes several tables into one workbook, one sheet each.
- `can_cast(from_type, to_type)`, `to_sql_type(field_type)` ask the runtime what casts are legal, rather than guessing.

`pipeline_runtime.py` raises one exception, `PipelineRuntimeError`, for anything that goes wrong while it's running. See **Indicating Errors**, below, for what you do with that.

## Requirements

Your language must let a program express everything below. How you write it, meaning keywords, punctuation, and statement order, is entirely your design choice. However, every capability below must be implemented; nothing here is optional.

### Reading Rules

- A program must be able to read a table from a CSV file, a JSON file, or a set of tables from an Excel workbook, given a path.
- Reading a CSV or JSON file produces exactly one named table.
- Reading an Excel file produces a **named table per sheet**. Referencing one sheet must be unambiguous, and syntactically distinct from referencing a single-table CSV/JSON source.

### Name Rules

The runtime normalizes every column name and sheet name it reads from a file: `"Full Name"` becomes `full_name`, and a sheet called `"Q1 Results"` becomes `q1_results`. Your program never normalizes anything. Every column or sheet name written in a program, whether it refers to an existing one or creates a new one (a map's target field, an aggregate's result field, a sheet a result is written under), is used exactly as written and must already be in normalized form. Your grammar must not accept a column or sheet name that isn't. So a reference has to be the normalized name, and working out what a file's header turns into is your validator's job.

**Read [A note on Names](a-note-on-names.md) before you design the token for column and sheet names.** It covers what normalizing does to a file's names, how two names that would collide are kept apart, and what a headerless file's columns are called.

### Filter Rules

- A program must be able to produce a new table containing only the rows of an existing table that satisfy a condition.
- Conditions must support `=`, `!=`, `<`, `<=`, `>`, `>=`, and membership in an explicit list of values.
- Conditions must support combining with AND, OR, and NOT, with NOT binding tightest, AND next, and OR loosest by default, plus a way to override that default with explicit grouping.
- A condition may reinterpret a field's type for the duration of one comparison only.

### Map Rules

- A program must be able to produce a new table where a chosen subset of an existing table's fields are renamed, retyped, or both.
- Any field not chosen must pass through unchanged.
- This is the only way to change a field's name or type persistently.

### Aggregate Rules

- A program must be able to produce a new table by grouping an existing table's rows on one or more fields and computing summary values per group.
- Your language must use exactly these four operation names, spelled exactly as shown: **SUM, COUNT, MIN, MAX**. These are fixed by `pipeline_runtime.py`'s `_AGGREGATE_FUNCTIONS`: `Table.aggregate` checks `AggregateCall.function` against these literal strings and rejects anything else. COUNT must support both "rows in the group" and "non-null values of one specific field."
- No expression may be given as a summary operation's input; only a single field, or, for counting rows, no field at all.

### Key Rules

- A program must be able to declare a reusable key mapping: an association from several already-bound tables to the field on each one that plays the join-key role.
- A key mapping needs exactly one field per table it names.
- Declaring a key mapping that's never used by a join is not an error; a join referencing a key mapping that was never declared is.

### Join Rules

- A program must be able to combine two or more existing tables into one new table, keeping a row only where each table's key-column value agrees.
- A join's key must come from either a previously-declared key mapping (above), referenced by name, or an inline key mapping written directly as part of the join. There is no other way to specify a join's key. A bare shared column name, with no key mapping at all, is not sufficient.
- Either way, the key mapping used must name exactly one column per table being joined. A table in the join missing from the key, or a key entry for a table not in the join, must be a reported error.
- Two tables sharing a non-key column name must be a reported error, not a silent overwrite.

### Subset Rules

- A program must be able to produce a new table containing only a chosen, ordered, non-empty, non-repeating list of an existing table's fields, dropping the rest.
- This operation must not rename or retype fields.

### Output Rules

- A program must be able to write an existing table's fields, in full, to a new CSV or JSON file.
- A program must be able to write one or more existing tables, each in full, to a new Excel workbook, one sheet per table, each under a sheet name the program supplies.
- An output path must never equal any path the same program read from.

### Type Rules

- Your language must use exactly these five type names, spelled exactly as shown: **bool, int, double, string, date**. These are fixed by `pipeline_runtime.py`'s `FIELD_TYPES`; `can_cast`, `to_sql_type`, and `FieldMapping.new_type` all check against these literal strings, so there's no freedom to rename them; wherever your syntax needs to write a type, it writes one of these five words verbatim.
- A field's type may be reinterpreted (cast) inline within one filter condition, or persistently as part of a map, and nowhere else.
- Every type may cast to and from `string`. `bool`, `int`, and `double` freely interconvert with each other. None of `bool`, `int`, or `double` may ever cast to or from `date`.
- Cast legality between two actual types must be checked using `can_cast`; don't re-derive the rule yourself.

### Immutability Rules

- Every table-producing operation must produce a new, independent value; it must never modify an existing one.
- An operation that reads like it updates an existing name (filtering, reshaping, grouping) must rebind that name to the new value, not mutate anything in place.
- A source file, once read, must never be a legal target for output later in the same program.

## Parsing and Executing the Script

Not every requirement above can be checked at the same point:

- **Grammar-level.** A program that doesn't match your grammar at all should fail to parse. Lark handles this for you. For example, a column or sheet name that isn't in normalized form belongs here: the token for those names should simply not match it.
- **Tier 1 (structural).** Checkable directly from the parse tree, before touching any file. For example, an undeclared name, a name declared twice, a source path read more than once, a join whose key mapping doesn't cover every table listed (or vice versa), an output path equal to an input path, an empty or repeating column list, a cast to an unrecognized type name.
- **Tier 2 (schema).** Only checkable by inspecting a source's actual columns, using `pipeline_runtime.py`'s `*_schema` functions or `can_cast`. For example, a renamed, selected, or grouped field that doesn't exist, a sheet the workbook doesn't have, a cast the runtime rejects. Those functions report normalized names (see [A note on Names](a-note-on-names.md)), so compare references against those.
- **Tier 3 (runtime).** Only discoverable while actually executing, surfaced as `PipelineRuntimeError`. For example, a condition that fails on real data, a cast that fails on a specific value, a disk error while writing.

Your interpreter must validate a script completely before running anything, and must report **every** problem a tier finds, not just the first. Tier 2 runs only if Tier 1 has found nothing (a structural problem, such as an undeclared name, makes schema checks on that program meaningless). So one validation pass reports either every Tier 1 problem or every Tier 2 problem, never a mix. If validation passes, execution must run to completion or not at all.

## Indicating Errors with `pipeline_errors.py`

A supplied module, `pipeline_errors.py`, defines the exceptions you are to raise:

- `GrammarError` means the program doesn't parse.
- `StructuralError` marks a Tier 1 problem.
- `SchemaError` marks a Tier 2 problem.
- `ExecutionError` wraps a `PipelineRuntimeError` caught during execution (Tier 3).
- `ValidationErrors` collects every `StructuralError`/`SchemaError` your validation pass finds; raise exactly one of these at the end, wrapping the whole list.

Read `pipeline_errors.py`'s own docstrings for the intended usage pattern.

## Notes

- You may add any helper methods, classes, or instance state to your interpreter that you find useful. All code, however, should be in the `interpreter` module.

## Constraints

- **No user-definable functions.** This language is made up entirely of statements; tdo not define a function within your language.
- **No re-reading a source.** A program may not read the same source path more than once.

## Out of Scope

- Joining tables on more than one column.
- An exclusion-style column list ("everything except these fields") is not necessary.

## Example Scenario

Your language must be able to express the following, end to end:

- Two sources describe the same customers in different file formats, one CSV and one JSON, using differently-named identifier fields.
- Filter one source to active records, and the other to positive-balance records.
- Declare a key mapping naming each source's own identifier field, and join the two sources on it.
- Write the joined result out in full to a new CSV file
- Group the joined result by a category field, computing a per-category total and a per-category count.
- Narrow that summary to a few columns and write it out separately.
