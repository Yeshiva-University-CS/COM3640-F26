# PipelineQL: 10 flows / 20 points

Run from the `assignment-2` root. Use paths relative to that directory inside every `.ql` file:

- Input: `grading/data/filename.csv` (for example, `grading/data/people.csv`).
- Output: `grading/output/filename.csv` (for example, `grading/output/01-original.csv`).
- Use `.json` or `.xlsx` where specified.

Run the full suite with `uv run python grading/src/run_tests.py`.

Place `flow01.ql` through `flow10.ql` in `grading/ql/`, using your language syntax. Each flow is worth **2 points**. Provide only the programs; no writeups are required. Use the unchanged `main.py`, `pipelineql.lark`, and `interpreter.py`.

Run each flow fresh. Input files are under `grading/data/`; create `grading/output/` and use the output filenames below. Outputs are CSV unless stated otherwise. Read each source once per flow. Independent operations start from the original table; order operations as needed if your language rebinds names.

Preserve source column order unless another order is specified. Mapping replaces a field in its existing position. Row order, JSON member order, and workbook sheet order are not graded. Partial-credit details are in the scorecard.

## Flow 01. CSV and subset

1. Read `people.csv`; write it unchanged as `01-original.csv`.
2. Select `2024_total`, then `full_name`; write `01-subset.csv` in that column order.
3. Read `headerless.csv`; write its data to `01-headerless.csv` with the default column names as the header in their original order.

## Flow 02. JSON and casts

1. Read `accounts.json`; write it unchanged as `02-json.json`.
2. Read `cast_values.json`. Filter `amount`, temporarily interpreted as `int`, for values greater than 7. Write `02-temporary.csv`.
3. From the original cast-values table, map `amount` to `quantity` and cast it to `int` in the same entry. Write `02-mapped.csv`, with columns `id, quantity, note`.

## Flow 03. Excel

Read `sources.xlsx`. Write `03-workbook.xlsx` with sheet `q1_results` named `q1_report` and sheet `q2_results` named `q2_report`. Preserve each sheet's column order.

## Flow 04. Filters

Read `numbers.csv`. Independently filter the original table for each condition and write the named file:

| File | Condition |
|---|---|
| `04-equal.csv` | `value = 10` |
| `04-range.csv` | `value >= -5 AND value <= 20` |
| `04-outside.csv` | `value != 50 AND (value < 0 OR value > 30)` |
| `04-membership.csv` | `value` is one of `-10, 0, 25, 75` |

Read `logic.csv`. Independently filter the original table for:

| File | Condition |
|---|---|
| `04-default.csv` | `a = 1 OR b = 1 AND NOT c = 1`, without grouping |
| `04-grouped.csv` | `(a = 1 OR b = 1) AND NOT (c = 1)` |

Use your language's condition syntax.

## Flow 05. Aggregates

Read `sales.csv`. Independently produce:

1. `05-region.csv`: group by `region`; compute `SUM(amount)` as `total`, `MIN(amount)` as `smallest`, `MAX(amount)` as `largest`, COUNT of rows as `rows`, and COUNT of non-null `amount` values as `present`. Column order: `region, total, smallest, largest, rows, present`.
2. `05-category.csv`: group by `region`, then `category`; compute `SUM(amount)` as `total`. Column order: `region, category, total`.

## Flow 06. Joins

Read `left.csv`, `right.csv`, and `third.csv`.

1. Declare a named key mapping for the left table's `customer_id` and the right table's `account_id`. Join left and right using that mapping, listing left first. Select only `name, balance`, in that order, and write `06-named.csv`.
2. Independently join all three original tables in left/right/third order, using an inline mapping of `customer_id`, `account_id`, and `person_id`, respectively. Select only `name, balance, tier`, in that order, and write `06-inline.csv`.

## Flow 07. Invalid field spelling

Read `people.csv` and select the field spelled `Full_Name`, using your normal field-reference syntax.

## Flow 08. Undeclared tables

Write these operations in order:

1. Read `numbers.csv` as table `numbers`.
2. Select `id` from `missing_customers`; use result name `customer_ids` if required.
3. Independently select `id` from `missing_orders`; use result name `order_ids` if required.
4. Independently select `id` from `missing_products`; use result name `product_ids` if required.
5. Write the original `numbers` table to `08-should-not-exist.csv`.

Do not declare the three table names beginning with `missing_` or use the subset results in later operations. Use otherwise valid syntax.

## Flow 09. Invalid schema references

Use otherwise valid syntax for these independent operations:

1. Read `numbers.csv`; select `missing_field`.
2. Read `dates.csv`; map `joined` from `date` to `int`.
3. Read `left.csv` and `collision.csv`; join them using a complete inline mapping of `customer_id` and `account_id`.
4. Write the original numbers table to `09-should-not-exist.csv`.

Do not use any subset, map, or join result in another operation.

## Flow 10. Cast execution

Read `bad_cast.csv`; map `amount` to `int`; write that result to `10-should-not-exist.csv`.

Include the referenced table or field name in validation diagnostics. No particular message wording is required.
