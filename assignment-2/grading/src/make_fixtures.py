"""Build deterministic grading fixtures and independently specified expectations."""
import csv
import itertools
import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile
from flow_artifacts import REFERENCE_IDS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def workbook(path, sheets):
    """Write simple OOXML fixtures without installing an Excel library."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1))
        z.writestr('[Content_Types].xml', f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>{overrides}</Types>')
        z.writestr('_rels/.rels', f'<Relationships xmlns="{package}"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        entries = ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(sheets, 1))
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>{entries}</sheets></workbook>')
        links = ''.join(f'<Relationship Id="rId{i}" Type="{rel}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets)+1))
        z.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{package}">{links}</Relationships>')
        for i, rows in enumerate(sheets.values(), 1):
            xml_rows = []
            for row_number, row in enumerate(rows, 1):
                cells = []
                for col, value in enumerate(row):
                    address = f'{chr(65+col)}{row_number}'
                    if isinstance(value, str):
                        cells.append(f'<c r="{address}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
                    elif value is not None:
                        cells.append(f'<c r="{address}"><v>{value}</v></c>')
                xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')


def main():
    DATA.mkdir(exist_ok=True)
    expected = {}

    def fixture(name, columns, rows, header=True):
        with (DATA / name).open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if header:
                writer.writerow(columns)
            writer.writerows(rows)

    def result(ids, columns, rows):
        for case in ids.split():
            expected[case] = {'columns': columns, 'rows': rows}

    people = [[1, 'Ada', 'A', 12], [2, 'Ben', 'B', 20], [3, 'Cy', 'C', 8], [3, 'Cy', 'C', 8]]
    fixture('people.csv', ['Person ID', 'Full Name', 'full_name', '2024 Total'], people)
    result('01a', ['person_id', 'full_name', 'full_name_1', '2024_total'], people)
    headless = [[10, 100], [20, 200], [30, 300]]
    fixture('headerless.csv', [], headless, False)
    result('01b', ['col_a', 'col_b'], headless)
    accounts = [{'Account ID': 1, 'Balance': 10.5, 'Status': 'open'}, {'Account ID': 2, 'Balance': None, 'Status': 'closed'}, {'Account ID': 3, 'Balance': -2.5, 'Status': 'open'}]
    (DATA / 'accounts.json').write_text(json.dumps(accounts, indent=2)+'\n', encoding='utf-8')
    result('02a', ['account_id', 'balance', 'status'], [[1, 10.5, 'open'], [2, None, 'closed'], [3, -2.5, 'open']])
    workbook(DATA / 'sources.xlsx', {'Q1 Results': [['Item ID', 'Units'], [1, 10], [2, 20]], 'Q2 Results': [['Item ID', 'Units'], [3, 30], [4, 40]]})
    result('03a', ['item_id', 'units'], [[1, 10], [2, 20]])
    result('03b', ['item_id', 'units'], [[3, 30], [4, 40]])
    numbers = [
        [1, -20, 'negative'], [2, -10, 'negative'],
        [3, -5, 'negative'], [4, -1, 'negative'], [5, 0, 'zero'],
        [6, 2, 'low'], [7, 5, 'low'], [8, 10, 'middle'],
        [9, 10, 'middle'], [10, 15, 'middle'], [11, 20, 'middle'],
        [12, 25, 'high'], [13, 30, 'high'], [14, 40, 'high'],
        [15, 50, 'high'], [16, 75, 'high'], [17, 100, 'high'],
    ]
    fixture('numbers.csv', ['id', 'value', 'label'], numbers)
    for case, ids in {
        '04a': [8, 9],
        '04b': [1, 2, 3, 4, 14, 16, 17],
        '06a': [2, 5, 12, 16],
    }.items():
        rows = [row for row in numbers if row[0] in ids]
        result(case, ['id', 'value', 'label'], rows)
    logic = [[i, *bits] for i, bits in enumerate(itertools.product([0, 1], repeat=3), 1)]
    fixture('logic.csv', ['id', 'a', 'b', 'c'], logic)
    result('05a', ['id', 'a', 'b', 'c'], [logic[i-1] for i in [3, 5, 6, 7, 8]])
    result('05b', ['id', 'a', 'b', 'c'], [logic[i-1] for i in [3, 5, 7]])
    # JSON strings retain zero padding and allow legal int casts on every row.
    cast = [[1, '6', 'first'], [2, '020', 'second'], [3, '100', 'third']]
    (DATA / 'cast_values.json').write_text(json.dumps([dict(zip(['id', 'amount', 'note'], row)) for row in cast], indent=2)+'\n', encoding='utf-8')
    result('06b', ['id', 'amount', 'note'], cast[1:])
    result('07a 07b-mapped', ['id', 'reading', 'label'], numbers)
    result('07b-original 15a 15b', ['id', 'value', 'label'], numbers)
    result('08a', ['id', 'amount', 'note'], [[2, 20, 'second'], [3, 100, 'third']])
    result('08b', ['id', 'quantity', 'note'], [[1, 6, 'first'], [2, 20, 'second'], [3, 100, 'third']])
    sales = [['north', 'a', 10], ['north', 'a', 20], ['north', 'b', None], ['south', 'a', 5], ['south', 'b', 15], ['south', 'b', 15]]
    fixture('sales.csv', ['region', 'category', 'amount'], sales)
    result('09a', ['region', 'total', 'smallest', 'largest'], [['north', 30, 10, 20], ['south', 35, 5, 15]])
    result('09b', ['region', 'category', 'total'], [['north', 'a', 30], ['north', 'b', None], ['south', 'a', 5], ['south', 'b', 30]])
    result('10a', ['region', 'rows', 'present'], [['north', 3, 2], ['south', 3, 3]])
    left = [[1, 'Ada'], [2, 'Ben'], [3, 'Cy'], [4, 'Dee']]
    right = [[2, 20], [2, 25], [3, 30], [5, 50]]
    third = [[2, 'gold'], [3, 'silver'], [6, 'bronze']]
    fixture('left.csv', ['customer_id', 'name'], left)
    fixture('right.csv', ['account_id', 'balance'], right)
    fixture('third.csv', ['person_id', 'tier'], third)
    fixture('collision.csv', ['account_id', 'name'], [[2, 'other'], [3, 'another']])
    result('11a 12a', ['name', 'balance'], [['Ben', 20], ['Ben', 25], ['Cy', 30]])
    result('11b', ['name', 'balance', 'tier'], [['Ben', 20, 'gold'], ['Ben', 25, 'gold'], ['Cy', 30, 'silver']])
    result('12b', ['customer_id', 'name'], left)
    result('14a', ['2024_total', 'full_name'], [[r[3], r[1]] for r in people])
    expected['16a'] = {'sheets': {'customers': {'columns': ['customer_id', 'name'], 'rows': left}, 'tiers': {'columns': ['person_id', 'tier'], 'rows': third}}}
    fixture('dates.csv', ['id', 'joined'], [[1, '2024-01-15'], [2, '2024-02-20']])
    fixture('bad_cast.csv', ['id', 'amount'], [[1, '12'], [2, 'not_a_number']])
    expected = {new: expected[old] for new, old in REFERENCE_IDS.items()}
    expected['04-range'] = {
        'columns': ['id', 'value', 'label'],
        'rows': [row for row in numbers if row[0] in [3, 4, 5, 6, 7, 8, 9, 10, 11]],
    }
    expected['03-workbook'] = {'sheets': {
        'q1_report': {'columns': ['item_id', 'units'], 'rows': [[1, 10], [2, 20]]},
        'q2_report': {'columns': ['item_id', 'units'], 'rows': [[3, 30], [4, 40]]},
    }}
    expected['05-region'] = {
        'columns': ['region', 'total', 'smallest', 'largest', 'rows', 'present'],
        'rows': [['north', 30, 10, 20, 3, 2], ['south', 35, 5, 15, 3, 3]],
    }
    (ROOT / 'src' / 'expected.json').write_text(json.dumps(expected, indent=2)+'\n', encoding='utf-8')
    print(f'Generated fixtures in {DATA} and {len(expected)} output expectations.')


if __name__ == '__main__':
    main()
