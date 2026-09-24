"""
pipeline_runtime: the supplied runtime library for the PipelineQL assignment.

This module is the ONLY place file I/O, type inference, joining, and aggregation
happen. Your code should never open a file, call DuckDB directly, or hand-roll
CSV/JSON/Excel parsing. You should build an AST from the PipelineQL grammar, then
call into this module to execute it.

"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from typing import NamedTuple

import duckdb


__all__ = [
    "FIELD_TYPES",
    "PipelineRuntimeError",
    "normalize_name",
    "can_cast",
    "to_sql_type",
    "read_csv_schema",
    "read_csv",
    "read_json_schema",
    "read_json",
    "read_excel_workbook_schema",
    "read_excel_workbook",
    "FieldMapping",
    "AggregateCall",
    "Table",
    "merge",
    "write_excel_workbook",
]


# ---------------------------------------------------------------------------
# field types
# ---------------------------------------------------------------------------

#: The five field-type aliases this language recognizes. A cast to any
#: name outside this set is a Tier 1 error that you should catch.
FIELD_TYPES: frozenset[str] = frozenset(
    {"bool", "int", "double", "string", "date"}
)

_ALIAS_TO_SQL: dict[str, str] = {
    "bool": "BOOLEAN",
    "int": "BIGINT",
    "double": "DOUBLE",
    "string": "VARCHAR",
    "date": "DATE",
}
_SQL_TO_ALIAS: dict[str, str] = {v: k for k, v in _ALIAS_TO_SQL.items()}

# Restricting read_csv_auto's own candidate set to exactly these SQL types
# keeps a source field's inferred type always mappable back to one of
# FIELD_TYPES. DuckDB's own default candidate set additionally includes
# NULL, TIME, TIMESTAMP, and TIMESTAMPTZ, none of which this language has.
_AUTO_TYPE_CANDIDATES = ["BOOLEAN", "BIGINT", "DOUBLE", "DATE", "VARCHAR"]

# One representative, non-null literal per type, used to ask DuckDB whether a
# cast is legal at the type level (see module docstring). Must not be NULL;
# DuckDB folds CAST(NULL::X AS Y) through for any X/Y without truly checking
# the pair.
_CAST_PROBE_LITERALS = {
    "bool": "TRUE",
    "int": "1",
    "double": "1.0",
    "date": "DATE '2000-01-01'",
}

_AGGREGATE_FUNCTIONS = {"SUM", "COUNT", "MIN", "MAX"}


class PipelineRuntimeError(Exception):
    """
    Raised for any failure this library encounters: a missing file, an illegal
    cast, a join key that doesn't exist, a malformed value at execution time,
    and so on.
    """


# ---------------------------------------------------------------------------
# internal: shared connection + small helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_connection: duckdb.DuckDBPyConnection | None = None
_excel_loaded = False


def _get_connection() -> duckdb.DuckDBPyConnection:
    global _connection
    with _lock:
        if _connection is None:
            _connection = duckdb.connect(":memory:")
        return _connection


def _rollback_after_error() -> None:
    """
    Clear the shared connection's aborted-transaction state.

    A statement that fails while executing (a missing file, a value that
    won't convert) leaves DuckDB's implicit transaction aborted. Every later
    statement on that connection then fails with "Current transaction is
    aborted" no matter what it actually asked for, which would make one bad
    file look like a broken workbook three statements later. A validator is
    expected to report every problem it finds rather than stopping at the
    first, so a failure here must not make the next call lie.

    The rollback raises when there is nothing to roll back, which is the
    common case (a planning error aborts nothing) and is ignored.
    """
    try:
        _get_connection().rollback()
    except duckdb.Error:
        pass


def _ensure_excel_extension() -> None:
    global _excel_loaded
    if _excel_loaded:
        return
    con = _get_connection()
    try:
        con.execute("INSTALL excel")
        con.execute("LOAD excel")
        _excel_loaded = True
    except duckdb.Error as e:
        _rollback_after_error()
        raise PipelineRuntimeError(
            f"could not load DuckDB's excel extension: {e}"
        ) from e


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(path: str) -> str:
    return "'" + path.replace("'", "''") + "'"


def _sql_type_to_alias(sql_type_str: str) -> str:
    base = sql_type_str.upper()
    if base not in _SQL_TO_ALIAS:
        raise PipelineRuntimeError(
            f"column has type {sql_type_str}, which is outside this "
            f"language's five field types; check "
            f"auto_type_candidates or the source data"
        )
    return _SQL_TO_ALIAS[base]


def _widen_unrecognized_types_to_string(
    rel: "duckdb.DuckDBPyRelation",
) -> "duckdb.DuckDBPyRelation":
    """
    Widen any column whose inferred SQL type isn't one of FIELD_TYPES to
    VARCHAR, rather than leaving it to surface later as a confusing
    PipelineRuntimeError the first time something reads this table's schema.

    CSV already avoids this at the source, via `_AUTO_TYPE_CANDIDATES`
    restricting what `read_csv_auto` will ever guess. `read_json_auto` and
    `read_xlsx` have no equivalent restriction, though, and both readily
    infer TIME or TIMESTAMP on their own. A JSON string that looks like a
    time-of-day, or an Excel cell formatted as one, will do it. So this
    is the backstop for those two paths (and a defensive backstop for CSV
    too, if DuckDB's own candidate set ever changes).
    """
    unrecognized = {
        c for c, t in zip(rel.columns, rel.types) if str(t).upper() not in _SQL_TO_ALIAS
    }
    if not unrecognized:
        return rel
    select_list = ", ".join(
        f"CAST({_quote_ident(c)} AS VARCHAR) AS {_quote_ident(c)}"
        if c in unrecognized
        else _quote_ident(c)
        for c in rel.columns
    )
    return rel.project(select_list)


def _relation_schema(relation: "duckdb.DuckDBPyRelation") -> dict[str, str]:
    return {
        col: _sql_type_to_alias(str(t))
        for col, t in zip(relation.columns, relation.types)
    }


def _normalize_all(raw_names: list[str]) -> list[str]:
    """
    Normalize a group of file-provided names, keeping them distinct.

    Normalization can map two different raw names onto one identifier:
    "Full Name" and "full_name" both become full_name. Keeping only one
    would silently lose a whole column, so later duplicates take a numeric
    suffix instead. The first keeps `full_name`, the next becomes
    `full_name_1`, the one after that `full_name_2`.

    Args:
        raw_names: the names exactly as they appear in the file.

    Returns:
        The normalized names, positionally matching `raw_names`, all
        distinct from one another.
    """
    used: set[str] = set()
    result: list[str] = []
    for raw in raw_names:
        base = normalize_name(raw)
        name = base
        suffix = 0
        while name in used:
            suffix += 1
            name = f"{base}_{suffix}"
        used.add(name)
        result.append(name)
    return result


def _normalized_relation_schema(
    rel: "duckdb.DuckDBPyRelation",
) -> dict[str, str]:
    """`_relation_schema`, with every column name normalized."""
    raw = _relation_schema(rel)
    normalized = _normalize_all(list(raw))
    return dict(zip(normalized, raw.values()))


def _renamed_projection(rel: "duckdb.DuckDBPyRelation") -> "duckdb.DuckDBPyRelation":
    """Project rel with every column renamed to its normalized form."""
    normalized = _normalize_all(list(rel.columns))
    select_list = ", ".join(
        f"{_quote_ident(orig)} AS {_quote_ident(new)}"
        for orig, new in zip(rel.columns, normalized)
    )
    return rel.project(select_list)


# ---------------------------------------------------------------------------
# name normalization
# ---------------------------------------------------------------------------

def normalize_name(raw_name: str) -> str:
    """
    Apply the normalization rule to a single raw name: every character that
    isn't a letter, a digit, or an underscore becomes a separator, each run
    of separators collapses to a single underscore, leading and trailing
    underscores are dropped, and every letter is lowercased.

    The result contains only letters, digits, and underscores, which is the
    point: a grammar can match a normalized name with a single token, with
    no special case for whatever punctuation a spreadsheet happened to
    carry. A name may begin with a digit ("2024 Total" gives 2024_total);
    nothing is prefixed or rejected on that account, so a grammar's token
    for field and sheet names should allow it.

    Disallowed characters separate rather than vanish, so a name that leaned
    on one as its only word break still comes apart: "Q1(2024)" normalizes
    to q1_2024, not q12024, and "Order-Date" to order_date. Whatever sits
    between them is kept, so "Amount (USD)" gives amount_usd, while
    "Amount ($)" gives plain amount, the "$" leaving nothing behind.

    A name with nothing left once that is done ("()", say) would be no name
    at all, which is not something a reference could ever reach, so it
    becomes `unnamed`. Several of those in one file are told apart the same
    way any other repeat is, as unnamed, unnamed_1, unnamed_2.

    Args:
        raw_name: the exact string as it appears in the source file (a CSV/
            JSON column header, or an Excel sheet name).

    Returns:
        The normalized identifier. This function always succeeds: it never
        raises. If two names in one file normalize to the same thing, the
        reader keeps them apart by suffixing the later one (`full_name`,
        then `full_name_1`), so this function's result is the name only
        when it is the first to claim it.
    """
    s = re.sub(r"[^A-Za-z0-9_]+", " ", raw_name)
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    return s.strip("_").lower() or "unnamed"


def _require_normalized(name: str, what: str) -> None:
    """
    Reject a program-written column or sheet name that is not already in
    normalized form. A grammar is meant to make this unreachable: its token
    for column and sheet names should only match normalized names.
    """
    if normalize_name(name) != name:
        raise PipelineRuntimeError(
            f"invalid {what} {name!r}: it is not in normalized form (that "
            f"would be {normalize_name(name)!r}). Your grammar should have "
            f"rejected this program; its token for column and sheet names "
            f"must only match normalized names"
        )


# ---------------------------------------------------------------------------
# casting
# ---------------------------------------------------------------------------

def can_cast(from_type: str, to_type: str) -> bool:
    """
    Report whether a cast from `from_type` to `to_type` (both from
    FIELD_TYPES) is legal in the underlying runtime's type system.

    Your language should not have its own casting-compatibility
    table; it must ask the runtime directly, so the rules can't conflict with 
    what the runtime actually does.
    
    This function only says whether the *type pair* is legal, not whether a
    specific value will actually convert (e.g. `int("hello")` is a legal cast
    per this function, but fails on that particular value at Tier 3).

    Args:
        from_type: a member of FIELD_TYPES; the value's current type.
        to_type: a member of FIELD_TYPES; the requested type.

    Returns:
        True if the cast is supported at the type level; False otherwise.

    Raises:
        PipelineRuntimeError: if either argument isn't a member of FIELD_TYPES.
    """
    if from_type not in FIELD_TYPES:
        raise PipelineRuntimeError(f"{from_type!r} is not one of FIELD_TYPES")
    if to_type not in FIELD_TYPES:
        raise PipelineRuntimeError(f"{to_type!r} is not one of FIELD_TYPES")
    if from_type == to_type:
        return True
    if from_type == "string" or to_type == "string":
        return True
    con = _get_connection()
    literal = _CAST_PROBE_LITERALS[from_type]
    sql_to = _ALIAS_TO_SQL[to_type]
    try:
        con.sql(f"SELECT CAST({literal} AS {sql_to})").fetchall()
        return True
    except duckdb.Error:
        _rollback_after_error()
        return False


def to_sql_type(field_type: str) -> str:
    """
    Map one of this language's type aliases to the underlying SQL type name.

    Mapping:
        bool     -> BOOLEAN
        int      -> BIGINT
        double   -> DOUBLE
        string   -> VARCHAR
        date     -> DATE

    Args:
        field_type: a member of FIELD_TYPES.

    Returns:
        The corresponding SQL type name.

    Raises:
        PipelineRuntimeError: if `field_type` isn't a member of FIELD_TYPES.
    """
    if field_type not in FIELD_TYPES:
        raise PipelineRuntimeError(f"{field_type!r} is not one of FIELD_TYPES")
    return _ALIAS_TO_SQL[field_type]


# ---------------------------------------------------------------------------
# reading source files
#
# Every read_*_schema function below is schema-only: it inspects column names
# and types without loading full row data, so it's cheap enough to call during
# Tier 2 validation before deciding whether to execute anything. The
# read_* functions actually load data into Table(s), with column names already
# normalized. Schema functions report exactly the same normalized names, so
# whatever a caller checks a reference against during validation is what it
# will operate on.
#
# csv and json are always single-table. xlsx is always whole-workbook: a
# program is expected to read and write every sheet together, so there's no
# per-sheet read function. `read_excel_workbook` and
# `read_excel_workbook_schema` return every sheet at once, keyed by
# normalized sheet name.
# ---------------------------------------------------------------------------

def _synthetic_column_name(index: int) -> str:
    """
    0-indexed column position -> col_a, col_b, ..., col_z, col_aa, col_ab, ...
    (bijective base-26), used when a csv/xlsx source has no header row to
    take real names from.
    """
    letters = ""
    n = index
    while True:
        n, rem = divmod(n, 26)
        letters = chr(ord("a") + rem) + letters
        if n == 0:
            return f"col_{letters}"
        n -= 1


def _assign_synthetic_column_names(
    rel: "duckdb.DuckDBPyRelation",
) -> "duckdb.DuckDBPyRelation":
    select_list = ", ".join(
        f"{_quote_ident(orig)} AS {_quote_ident(_synthetic_column_name(i))}"
        for i, orig in enumerate(rel.columns)
    )
    return rel.project(select_list)


def _csv_has_header(path: str) -> bool:
    con = _get_connection()
    try:
        row = con.sql(
            f"SELECT HasHeader FROM sniff_csv({_quote_literal(path)})"
        ).fetchone()
    except duckdb.Error as e:
        _rollback_after_error()
        raise PipelineRuntimeError(f"could not read CSV {path!r}: {e}") from e
    return bool(row[0])


def _csv_relation(path: str) -> "duckdb.DuckDBPyRelation":
    con = _get_connection()
    candidates = "[" + ",".join(f"'{t}'" for t in _AUTO_TYPE_CANDIDATES) + "]"
    try:
        rel = con.sql(
            f"SELECT * FROM read_csv_auto({_quote_literal(path)}, "
            f"auto_type_candidates={candidates})"
        )
    except duckdb.Error as e:
        _rollback_after_error()
        raise PipelineRuntimeError(f"could not read CSV {path!r}: {e}") from e
    if not _csv_has_header(path):
        rel = _assign_synthetic_column_names(rel)
    return _widen_unrecognized_types_to_string(rel)


def read_csv_schema(path: str) -> dict[str, str]:
    """
    Return this CSV file's columns without loading its data: a dict mapping
    each normalized column name to its inferred field type (a member of
    FIELD_TYPES), in column order. Names are normalized here exactly as
    `read_csv` normalizes them, so the two always agree and a caller never
    has to deal with a file's raw spelling.

    If the file has no header row (detected via DuckDB's own CSV sniffer,
    not a heuristic reimplemented here), columns come back named col_a,
    col_b, col_c, ... by position instead of raising or misreading the first
    data row as a header.

    Args:
        path: filesystem path to a CSV file.

    Returns:
        Ordered dict of normalized column name -> field type.

    Raises:
        PipelineRuntimeError: if the file doesn't exist or can't be read.
    """
    return _normalized_relation_schema(_csv_relation(path))


def read_csv(path: str) -> "Table":
    """
    Load a CSV file as a Table. Column names are normalized
    before the Table is returned. A headerless file's synthetic col_a,
    col_b, ... names (see `read_csv_schema`) are already valid identifiers,
    so normalization leaves them untouched.

    Args:
        path: filesystem path to a CSV file.

    Returns:
        A new Table with one row per data row in the file.

    Raises:
        PipelineRuntimeError: if the file doesn't exist or can't be read.
    """
    return Table(_renamed_projection(_csv_relation(path)))


def _json_relation(path: str) -> "duckdb.DuckDBPyRelation":
    con = _get_connection()
    try:
        rel = con.sql(f"SELECT * FROM read_json_auto({_quote_literal(path)})")
    except duckdb.Error as e:
        _rollback_after_error()
        raise PipelineRuntimeError(f"could not read JSON {path!r}: {e}") from e
    return _widen_unrecognized_types_to_string(rel)


def read_json_schema(path: str) -> dict[str, str]:
    """
    Same as `read_csv_schema`, for a JSON file.

    Args:
        path: filesystem path to a JSON file.

    Returns:
        Ordered dict of normalized field name -> field type.

    Raises:
        PipelineRuntimeError: if the file doesn't exist or can't be read.
    """
    return _normalized_relation_schema(_json_relation(path))


def read_json(path: str) -> "Table":
    """
    Same as `read_csv`, for a JSON file.

    Args:
        path: filesystem path to a JSON file.

    Returns:
        A new Table.

    Raises:
        PipelineRuntimeError: if the file doesn't exist or can't be read.
    """
    return Table(_renamed_projection(_json_relation(path)))


def _excel_sheet_names(path: str) -> list[str]:
    # DuckDB's excel extension has no built-in function to list sheet names.
    # openpyxl gives this reliably and cheaply in read-only mode without 
    # loading cell data; every other excel operation still goes through DuckDB.
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        try:
            return list(wb.sheetnames)
        finally:
            wb.close()
    except FileNotFoundError as e:
        raise PipelineRuntimeError(f"could not read {path!r}: {e}") from e
    except Exception as e:
        raise PipelineRuntimeError(
            f"could not list sheets in {path!r}: {e}"
        ) from e


def _widen_integral_double_columns(
    rel: "duckdb.DuckDBPyRelation",
) -> "duckdb.DuckDBPyRelation":
    """
    Excel has no distinct integer storage type; DuckDB's `read_xlsx` always
    reports a numeric column as DOUBLE, even when every value in it is
    whole-numbered. Narrow any such column back to BIGINT (so it maps to
    this language's "int" rather than "double", matching how the same values
    would be typed coming from a CSV/JSON source) by asking the data itself
    whether every non-null value round-trips through TRUNC unchanged.
    """
    double_cols = [
        c for c, t in zip(rel.columns, rel.types) if str(t).upper() == "DOUBLE"
    ]
    if not double_cols:
        return rel
    checks = ", ".join(
        f"COALESCE(BOOL_AND({_quote_ident(c)} = TRUNC({_quote_ident(c)})), FALSE) "
        f"AS {_quote_ident(c)}"
        for c in double_cols
    )
    is_integral = dict(zip(double_cols, rel.aggregate(checks).fetchone()))
    if not any(is_integral.values()):
        return rel
    select_list = ", ".join(
        f"CAST({_quote_ident(c)} AS BIGINT) AS {_quote_ident(c)}"
        if is_integral.get(c)
        else _quote_ident(c)
        for c in rel.columns
    )
    return rel.project(select_list)


def _spreadsheet_column_letters(index: int) -> str:
    """0-indexed column position -> Excel-style column letters: A, B, ..., Z, AA, ..."""
    letters = ""
    n = index
    while True:
        n, rem = divmod(n, 26)
        letters = chr(ord("A") + rem) + letters
        if n == 0:
            return letters
        n -= 1


def _looks_like_headerless_excel(rel: "duckdb.DuckDBPyRelation") -> bool:
    """
    `read_xlsx` has its own header heuristic (mirroring the CSV reader's),
    but, unlike CSV's `sniff_csv(...).HasHeader`, doesn't expose whether
    it decided row 1 was a header through any queryable result. When it
    decides row 1 *isn't* one, it falls back to naming each column after its
    own spreadsheet cell address on row 1 (A1, B1, C1, ...), since there's no
    header text to use instead; that exact fallback pattern is the
    signal checked here.
    """
    expected = [f"{_spreadsheet_column_letters(i)}1" for i in range(len(rel.columns))]
    return list(rel.columns) == expected


def _excel_sheet_relation(path: str, sheet_name: str) -> "duckdb.DuckDBPyRelation":
    _ensure_excel_extension()
    con = _get_connection()
    try:
        rel = con.sql(
            f"SELECT * FROM read_xlsx({_quote_literal(path)}, "
            f"sheet={_quote_literal(sheet_name)})"
        )
        if _looks_like_headerless_excel(rel):
            rel = _assign_synthetic_column_names(rel)
        rel = _widen_integral_double_columns(rel)
        return _widen_unrecognized_types_to_string(rel)
    except duckdb.Error as e:
        _rollback_after_error()
        raise PipelineRuntimeError(
            f"could not read sheet {sheet_name!r} of {path!r}: {e}"
        ) from e


def read_excel_workbook_schema(path: str) -> dict[str, dict[str, str]]:
    """
    Return the schema of every sheet in an Excel workbook at once, without
    loading any row data: a dict mapping each normalized sheet name to that
    sheet's own schema, itself a dict mapping normalized column name to
    inferred field type (a member of FIELD_TYPES), in column order.

    Sheet names and column names are both normalized here, exactly as
    `read_excel_workbook` normalizes them, so the two always agree. Use this
    to resolve a program's reference to one sheet against what the
    workbook actually contains, without having to care how the sheet was
    spelled in the file.

    A sheet with no header row (detected from DuckDB's own read behavior,
    not a heuristic reimplemented here) gets columns named col_a, col_b,
    col_c, ... by position, the same as a headerless CSV (`read_csv_schema`).

    Args:
        path: filesystem path to an .xlsx file.

    Returns:
        Ordered dict of normalized sheet name -> (ordered dict of normalized
        column name -> field type).

    Raises:
        PipelineRuntimeError: if the file doesn't exist or can't be read.
    """
    raw_sheets = _excel_sheet_names(path)
    sheet_names = _normalize_all(raw_sheets)
    result: dict[str, dict[str, str]] = {}
    for raw_sheet, sheet_name in zip(raw_sheets, sheet_names):
        rel = _excel_sheet_relation(path, raw_sheet)
        result[sheet_name] = _normalized_relation_schema(rel)
    return result


def read_excel_workbook(path: str) -> dict[str, "Table"]:
    """
    Load every sheet of an Excel workbook at once: a dict mapping each
    normalized sheet name to a Table for that sheet, with column names
    normalized too. Every name a file supplies is normalized by the reader,
    so a program's reference to one sheet can be resolved against this
    dict's keys directly.

    There's no per-sheet variant; a program always reads an entire
    workbook's sheets together, so this is the only way `xlsx` sources are
    loaded.

    Args:
        path: filesystem path to an .xlsx file.

    Returns:
        Ordered dict of normalized sheet name -> Table.

    Raises:
        PipelineRuntimeError: if the file doesn't exist or can't be read.
    """
    raw_sheets = _excel_sheet_names(path)
    sheet_names = _normalize_all(raw_sheets)
    result: dict[str, "Table"] = {}
    for raw_sheet, sheet_name in zip(raw_sheets, sheet_names):
        rel = _excel_sheet_relation(path, raw_sheet)
        result[sheet_name] = Table(_renamed_projection(rel))
    return result


# ---------------------------------------------------------------------------
# Structured arguments for Table.map and Table.aggregate
# ---------------------------------------------------------------------------

class FieldMapping(NamedTuple):
    """
    One renaming entry in a map.

    Attributes:
        old_name: the field's current name, spelled exactly as the table
            already carries it, which means already normalized.
        new_name: the name it should have afterward (may equal old_name if
            only the type is changing). Must already be in normalized form
            (see `normalize_name`); a name that is not, such as
            "Customer ID", is rejected rather than normalized.
        new_type: a member of FIELD_TYPES if this renaming also casts the
            field, or None for a plain rename with no type change.
    """
    old_name: str
    new_name: str
    new_type: str | None = None


class AggregateCall(NamedTuple):
    """
    One summary entry in an aggregate.

    Attributes:
        function: one of "SUM", "COUNT", "MIN", "MAX".
        argument: a field name, spelled exactly as the table already
            carries it, or the literal string "*", only legal when
            function == "COUNT".
        output_name: the name this aggregate's result column should have.
            Must already be in normalized form (see `normalize_name`); a
            name that is not is rejected rather than normalized.
    """
    function: str
    argument: str
    output_name: str


# ---------------------------------------------------------------------------
# Table: the value every table-producing statement in a PipelineQL
# program (read, filter, map, join, aggregate, subset) binds a name to.
# ---------------------------------------------------------------------------

class Table:
    """
    An immutable, DuckDB-backed table.

    Table instances are only ever produced by this module, by the `read_*`
    functions above, by `merge()` below, or by calling one of the methods on
    an existing Table. There is no public way to construct one directly.

    Every method below returns a *new* Table rather than modifying the
    receiver, matching the rebind-don't-mutate rule: calling `.filter(...)`
    on a Table does not change it, it produces a new, independent Table, the
    same way a filter in the language rebinds a name to a new value rather
    than mutating the old one.

    Because the underlying DuckDB relation is lazy, building a chain of
    `.filter()` / `.map()` / `.aggregate()` calls (and reading `.schema` along
    the way) does not require materializing any row data, which is what
    makes it cheap enough to do during Tier 2 validation, before a
    `write_*` call actually forces anything to run.
    """

    def __init__(self, relation: "duckdb.DuckDBPyRelation") -> None:
        """
        Not part of the public interface. Table instances are constructed
        internally by this module's reading, merging, and transformation
        functions; student code should never call this directly.
        """
        self._relation = relation

    @property
    def schema(self) -> dict[str, str]:
        """
        This table's current fields, in column order: a dict mapping each
        field name to its field type (a member of FIELD_TYPES).

        Use this for Tier 2 checks: does a field a statement names actually
        exist on this binding, and what's its type, without needing to
        force any row data to materialize.
        """
        return _relation_schema(self._relation)

    def filter(self, condition_sql: str) -> "Table":
        """
        Apply a filter: keep only rows for which `condition_sql`
        evaluates true.

        `condition_sql` is a SQL boolean expression built by the student's
        own code from a parsed condition. This method does not parse
        PipelineQL syntax itself, only ordinary SQL. Field names in the
        condition should match this table's current `.schema` keys exactly.
        Translate the parsed condition into SQL: `AND` / `OR` / `NOT`, the
        six comparison operators, and `IN (...)` all exist in SQL under
        those spellings; a per-comparison type reinterpretation becomes
        `CAST(expr AS SQL_TYPE)`, using `to_sql_type` for SQL_TYPE. String
        literals must be single-quoted in the generated SQL, whatever your
        language's own string syntax is.

        Args:
            condition_sql: a SQL boolean expression referencing this table's
                current field names.

        Returns:
            A new Table containing only the matching rows. `self` is
            unchanged.

        Raises:
            PipelineRuntimeError: if `condition_sql` isn't valid SQL, refers
                to a field that doesn't exist, or fails at evaluation time
                against the actual data (Tier 3).
        """
        try:
            return Table(self._relation.filter(condition_sql))
        except duckdb.Error as e:
            _rollback_after_error()
            raise PipelineRuntimeError(f"filter failed: {e}") from e

    def map(self, mappings: list["FieldMapping"]) -> "Table":
        """
        Apply a map: rename (and optionally cast) the given fields.
        Any current field not named in `mappings` passes through unchanged,
        under its existing name and type.

        Args:
            mappings: the renamings (and optional casts) to apply. Each
                `FieldMapping.old_name` must match a current field on this
                table exactly; references are not normalized for you, so
                ask for the normalized name the table actually carries.

        Returns:
            A new Table with the renamed/retyped schema. `self` is unchanged.

        Raises:
            PipelineRuntimeError: if an `old_name` doesn't exist on this
                table, a `new_name` isn't in normalized form, or a requested
                cast isn't legal (see `can_cast`) or fails on the actual
                data (Tier 3).
        """
        schema = self.schema
        # Nothing here is normalized. `new_name` becomes a column name, so
        # it must already be in normalized form; `old_name` is a reference,
        # looked up exactly as given, since every column already carries its
        # normalized name. Working out what to write is the caller's job.
        by_old = {m.old_name: m for m in mappings}
        for m in mappings:
            _require_normalized(m.new_name, "map target name")
            if m.old_name not in schema:
                raise PipelineRuntimeError(
                    f"map references field {m.old_name!r}, which does not "
                    f"exist on this table (has: {list(schema)})"
                )
            if m.new_type is not None:
                if m.new_type not in FIELD_TYPES:
                    raise PipelineRuntimeError(
                        f"{m.new_type!r} is not one of this language's "
                        f"five field types"
                    )
                if not can_cast(schema[m.old_name], m.new_type):
                    raise PipelineRuntimeError(
                        f"cannot cast field {m.old_name!r} from "
                        f"{schema[m.old_name]!r} to {m.new_type!r}"
                    )

        select_parts = []
        for old_name in schema:
            if old_name in by_old:
                m = by_old[old_name]
                expr = _quote_ident(old_name)
                if m.new_type is not None:
                    expr = f"CAST({expr} AS {to_sql_type(m.new_type)})"
                select_parts.append(f"{expr} AS {_quote_ident(m.new_name)}")
            else:
                select_parts.append(
                    f"{_quote_ident(old_name)} AS {_quote_ident(old_name)}"
                )
        try:
            projected = self._relation.project(", ".join(select_parts))
        except duckdb.Error as e:
            _rollback_after_error()
            raise PipelineRuntimeError(f"map failed: {e}") from e

        cast_columns = [m.new_name for m in mappings if m.new_type is not None]
        if cast_columns:
            # A DuckDB relation is lazy, so the projection above builds
            # without touching a single value. A cast that `can_cast` allows
            # at the type level but that some actual value fails (a `string`
            # field holding "hello" cast to `int`, say) would then surface
            # much later, whenever something first materializes this table:
            # as a raw DuckDB error, blamed on whatever statement happened to
            # trigger it rather than on this map, and possibly after an
            # earlier output had already written a file. Your language
            # requires the opposite: a pipeline runs to completion or not
            # at all. So force
            # the cast here and report it as a Tier 3 failure of this map.
            #
            # COUNT over each cast column is what forces evaluation. COUNT(*)
            # on its own is optimized past the CAST and validates nothing,
            # and a LIMIT-based probe would only see the first rows.
            probe = ", ".join(
                f"COUNT({_quote_ident(c)})" for c in cast_columns
            )
            try:
                projected.aggregate(probe).fetchall()
            except duckdb.Error as e:
                _rollback_after_error()
                raise PipelineRuntimeError(f"map failed: {e}") from e

        return Table(projected)

    def aggregate(
        self, group_by: list[str], aggregates: list["AggregateCall"]
    ) -> "Table":
        """
        Apply an aggregate: group rows by `group_by` and compute
        each entry in `aggregates` per group. The resulting table's fields
        are exactly `group_by` plus each `AggregateCall.output_name`; no
        other fields survive.

        Args:
            group_by: field names to group on (one or more), each spelled
                exactly as this table carries it.
            aggregates: the aggregate calls to compute per group.

        Returns:
            A new Table with one row per distinct group. `COUNT` always
            yields `int`; `MIN`/`MAX` keep their argument's type; `SUM`
            yields `int` over an `int` or `bool` field and `double` over a
            `double` one.

        Raises:
            PipelineRuntimeError: if a `group_by` field or an aggregate's
                `argument` field doesn't exist on this table, an
                `output_name` isn't in normalized form, or an aggregate's
                `argument` isn't numeric where the function requires it
                (Tier 3, unless the runtime can determine this from the
                schema alone).
        """
        schema = self.schema
        # Nothing here is normalized. `output_name` becomes a column name,
        # so it must already be in normalized form; `group_by` and each
        # call's `argument` are references, looked up exactly as given.
        for call in aggregates:
            _require_normalized(call.output_name, "aggregate output name")
        for g in group_by:
            if g not in schema:
                raise PipelineRuntimeError(
                    f"aggregate group-by references field {g!r}, which does not "
                    f"exist on this table"
                )
        select_parts = [_quote_ident(g) for g in group_by]
        for call in aggregates:
            if call.function not in _AGGREGATE_FUNCTIONS:
                raise PipelineRuntimeError(
                    f"unsupported aggregate function {call.function!r}"
                )
            if call.argument == "*":
                if call.function != "COUNT":
                    raise PipelineRuntimeError(
                        "'*' is only a valid argument for COUNT"
                    )
                arg_sql = "*"
            else:
                if call.argument not in schema:
                    raise PipelineRuntimeError(
                        f"{call.function}({call.argument}) references field "
                        f"{call.argument!r}, which does not exist on this "
                        f"table"
                    )
                arg_sql = _quote_ident(call.argument)
            agg_sql = f"{call.function}({arg_sql})"
            if call.function == "SUM" and schema.get(call.argument) in (
                "int",
                "bool",
            ):
                # DuckDB widens SUM over an integral column to HUGEINT, which
                # is outside this language's five field types; left
                # alone it would surface as a confusing PipelineRuntimeError
                # the first time anything read the result's schema. Cast back
                # to `int`'s SQL type so the result stays describable.
                agg_sql = f"CAST({agg_sql} AS {_ALIAS_TO_SQL['int']})"
            select_parts.append(
                f"{agg_sql} AS {_quote_ident(call.output_name)}"
            )

        try:
            group_cols = ", ".join(_quote_ident(g) for g in group_by)
            new_rel = self._relation.aggregate(
                ", ".join(select_parts), group_cols
            )
            return Table(new_rel)
        except duckdb.Error as e:
            _rollback_after_error()
            raise PipelineRuntimeError(f"aggregate failed: {e}") from e

    def select(self, columns: list[str]) -> "Table":
        """
        Apply a subset: keep only the given fields, in the given
        order, dropping everything else. Produces a new `Table` you can
        keep transforming. Writing a table out has no column selection of
        its own; `select` first if you only want to write out some fields.

        Args:
            columns: field names to keep, in the order they should appear on
                the result, each spelled exactly as this table carries it.

        Returns:
            A new Table with exactly these fields. `self` is unchanged.

        Raises:
            PipelineRuntimeError: if a name in `columns` doesn't exist on
                this table (Tier 2).
        """
        schema = self.schema
        for c in columns:
            if c not in schema:
                raise PipelineRuntimeError(
                    f"select references field {c!r}, which does not exist "
                    f"on this table (has: {list(schema)})"
                )
        select_sql = ", ".join(_quote_ident(c) for c in columns)
        try:
            return Table(self._relation.project(select_sql))
        except duckdb.Error as e:
            _rollback_after_error()
            raise PipelineRuntimeError(f"select failed: {e}") from e

    def write_csv(self, path: str) -> None:
        """
        Write this table to a CSV file: the single-table form of
        output. Every current field is written, in the table's own
        column order. Output has no column selection of its own, so
        `select` first if you only want some fields written.

        Args:
            path: destination file path. Overwritten if it already exists.
                (Whether `path` illegally collides with a path the program
                read from is a Tier 1 check: your validator's
                job, not this method's.)

        Raises:
            PipelineRuntimeError: if the file can't be written (Tier 3).
        """
        con = _get_connection()
        con.register("_src", self._relation)
        try:
            con.sql(
                f"COPY (SELECT * FROM _src) TO "
                f"{_quote_literal(path)} (FORMAT csv, HEADER true)"
            )
        except duckdb.Error as e:
            _rollback_after_error()
            raise PipelineRuntimeError(f"could not write CSV {path!r}: {e}") from e
        finally:
            con.unregister("_src")

    def write_json(self, path: str) -> None:
        """
        Same as `write_csv`, writing JSON instead.
        """
        con = _get_connection()
        con.register("_src", self._relation)
        try:
            con.sql(
                f"COPY (SELECT * FROM _src) TO "
                f"{_quote_literal(path)} (FORMAT json, ARRAY true)"
            )
        except duckdb.Error as e:
            _rollback_after_error()
            raise PipelineRuntimeError(f"could not write JSON {path!r}: {e}") from e
        finally:
            con.unregister("_src")


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------

def merge(bindings: list[tuple["Table", str]]) -> "Table":
    """
    Apply a join: combine two or more tables, keeping only rows whose
    key value matches across every listed table.

    The result's join-key column takes the name it had on the *first* listed
    table. If any two input tables share a *non-key* field name,
    this raises rather than silently picking one or renaming. Map one side
    to a distinct name first.

    Args:
        bindings: two or more (table, key_field_name) pairs. `key_field_name`
            is the name of the join-key field *on that particular table*,
            spelled exactly as that table carries it, already resolved by
            your code from the key mapping the join used, whether
            a previously declared one or one written inline; this function
            doesn't know or care which form produced it.

    Returns:
        A new Table containing the joined rows.

    Raises:
        PipelineRuntimeError: if fewer than two bindings are given, a
            `key_field_name` doesn't exist on its table, or two input tables
            share a non-key field name.
    """
    if len(bindings) < 2:
        raise PipelineRuntimeError("merge requires at least two bindings")

    for table, key in bindings:
        if key not in table.schema:
            raise PipelineRuntimeError(
                f"merge key {key!r} does not exist on one of the input tables"
            )

    canonical_key = bindings[0][1]
    aliases = [f"m{i}" for i in range(len(bindings))]
    non_key_cols: dict[str, list[str]] = {}
    for alias, (table, key) in zip(aliases, bindings):
        non_key_cols[alias] = [c for c in table.schema if c != key]

    seen: dict[str, str] = {}
    for alias, cols in non_key_cols.items():
        for c in cols:
            if c in seen:
                raise PipelineRuntimeError(
                    f"merge: field {c!r} exists on more than one input "
                    f"table (non-key column collision); map it to a "
                    f"unique name on at least one side before merging"
                )
            seen[c] = alias

    try:
        first_alias, first_key = aliases[0], bindings[0][1]
        current = bindings[0][0]._relation.set_alias(first_alias)
        for alias, (table, key) in zip(aliases[1:], bindings[1:]):
            other = table._relation.set_alias(alias)
            condition = (
                f"{first_alias}.{_quote_ident(first_key)} = "
                f"{alias}.{_quote_ident(key)}"
            )
            current = current.join(other, condition, how="inner")

        select_parts = [
            f"{first_alias}.{_quote_ident(canonical_key)} AS "
            f"{_quote_ident(canonical_key)}"
        ]
        for alias, cols in non_key_cols.items():
            for c in cols:
                select_parts.append(
                    f"{alias}.{_quote_ident(c)} AS {_quote_ident(c)}"
                )
        result = current.project(", ".join(select_parts))
        return Table(result)
    except duckdb.Error as e:
        _rollback_after_error()
        raise PipelineRuntimeError(f"merge failed: {e}") from e


# ---------------------------------------------------------------------------
# output, multi-table (xlsx)
# ---------------------------------------------------------------------------

def write_excel_workbook(sheets: dict[str, "Table"], path: str) -> None:
    """
    Write several tables into one Excel workbook as separate sheets. The
    multi-table form of output, and the only way this language
    produces `.xlsx` output. Symmetric with `read_excel_workbook`: the same
    dict shape works whether `sheets` is an unmodified (or partially
    transformed) result of reading a workbook, or an arbitrary bag of
    independently-produced tables assembled by one output statement.

    This call is atomic: either every sheet is written successfully and
    `path` ends up containing all of them, or none of them are; `path` is
    left exactly as it was before the call. Rows
    are assembled into the workbook with `openpyxl` rather than DuckDB's own
    `COPY ... (format 'xlsx', mode 'append')`. That mode is documented to
    add a new sheet to an existing file, but on this platform it instead
    silently rewrites the file from scratch on every call, discarding
    whatever sheets earlier calls had written (verified directly against the
    installed `excel` extension build; not a documented limitation). This
    function stages the whole write to a temporary file in the same
    directory as `path` and only moves it into place once every sheet has
    been written successfully.

    Args:
        sheets: dict mapping sheet name -> Table, one entry per sheet to
            write. Each key must already be in normalized form (see
            `normalize_name`), so that it reads back under exactly the name
            it was written under; a key that is not, such as "Q1 Results",
            is rejected rather than normalized.
        path: destination file path. Overwritten if it already exists.
            (Whether `path` illegally collides with a path the program
            read from is a Tier 1 check: your validator's job,
            not this function's.)

    Raises:
        PipelineRuntimeError: if `sheets` is empty, a sheet name isn't in
            normalized form, or the write fails partway through (in which
            case `path` is left unchanged, per the guarantee above).
    """
    if not sheets:
        raise PipelineRuntimeError("write_excel_workbook requires at least one sheet")
    # A sheet name is a name the program writes, so like a map target it
    # must already be in normalized form. The reader normalizes on the way
    # back in, and a name already in normalized form comes back unchanged,
    # so a workbook this program writes stays referenceable by the same
    # program under exactly the names it wrote.
    for name in sheets:
        _require_normalized(name, "sheet name")

    import openpyxl

    tmp_dir = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=tmp_dir)
    os.close(fd)
    os.remove(tmp_path)  # openpyxl must create this fresh

    try:
        wb = openpyxl.Workbook()
        placeholder = wb.active
        for i, (sheet_name, table) in enumerate(sheets.items()):
            try:
                columns = table._relation.columns
                rows = table._relation.fetchall()
            except duckdb.Error as e:
                _rollback_after_error()
                raise PipelineRuntimeError(
                    f"could not write workbook {path!r}: {e}"
                ) from e
            ws = placeholder if i == 0 else wb.create_sheet()
            ws.title = sheet_name
            ws.append(columns)
            for row in rows:
                ws.append(row)
        wb.save(tmp_path)
        os.replace(tmp_path, path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if isinstance(e, PipelineRuntimeError):
            raise
        raise PipelineRuntimeError(f"could not write workbook {path!r}: {e}") from e
