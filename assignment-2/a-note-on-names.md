# A note on Names

**The runtime normalizes every column name and sheet name it reads from a file.** Your code, however, never normalizes anything. Every column or sheet name you refer to or use in a program writes is used exactly as written, so it must already be in normalized form.  Your grammar must, therefore, not accept a denormalized name.

## What the reader does

Every column header (csv, json, excel) and every Excel sheet name is normalized as the file is read. Anything that is not a letter, a digit, or an underscore is treated as a word break; each run of those collapses to a single underscore; leading and trailing underscores are dropped; and every letter is lowercased.

| In the file | Becomes |
| --- | --- |
| `Field 1` | `field_1` |
| `Full Name` | `full_name` |
| `Q1 Results` | `q1_results` |
| `Amount (USD)` | `amount_usd` |
| `Q1(2024)` | `q1_2024` |
| `Order-Date` | `order_date` |
| `Amount ($)` | `amount` |
| `2024 Total` | `2024_total` |
| `()` | `unnamed` |

Punctuation separates rather than vanishing, so `Q1(2024)` gives `q1_2024`, not `q12024`, while `Amount ($)` is simply `amount`, with no trailing underscore. A header with nothing left, such as `()` or `$$$`, becomes `unnamed`. A name that starts with a digit stays that way.

Two more things the reader handles, both of which change what a column is called:

- **Two names that normalize alike are kept apart.** `Full Name` and `full_name` both want to be `full_name`; the first keeps it and each later one takes a numeric suffix: `full_name`, `full_name_1`, `full_name_2`. If a suffix would clash with a name the file already uses, it counts past it. Names are assigned left to right, so a column's name never depends on what comes after it. Sheet names in a workbook behave the same way.
- **A file with no header row** (a headerless CSV or Excel sheet) gets names by position: `col_a`, `col_b`, through `col_z`, then `col_aa`, `col_ab`, and so on.

The `*_schema` functions and the `read_*` functions report exactly the same normalized names, so whatever you check a reference against during validation is what you will operate on. `normalize_name` is exported from `pipeline_runtime.py` so your validator can work out what a header will be called without reimplementing the rule.

## What that means for your language

A column or sheet name in a program is looked up, or created, exactly as written. Nothing corrects it for you. A reference to the column headed `Full Name` has to say `full_name`. A map that creates a column has to name it `customer_id`, not `Customer ID`; the same goes for an aggregate's result field and for the sheet name a result is written under. The runtime rejects a created name that is not in normalized form rather than fixing it, so a program that ever sees that error has a grammar that let something through.

So your grammar's token for column and sheet names should match exactly the normalized shape and nothing else: lowercase letters, digits, and underscores, with no leading or trailing underscore. It must allow a leading digit, since `2024_total` is a legitimate column name, which a typical identifier token would refuse. Column and sheet names are a different kind of thing from the names you invent for sources and key mappings, and it is reasonable for the grammar to say so.

Nothing else is normalized. A **file path** is used exactly as written, so a source read from `"My Data (2024).csv"` looks for that file under that name. The name you **bind a source to** and the name you give a **key mapping** are yours to spell however your language defines identifiers. The rule reaches columns and sheets, and stops there.
