"""Materialize expected.json as reference files for the ten-flow assessment."""
import csv
import json
from pathlib import Path

from check_output import check
from make_fixtures import workbook

ROOT = Path(__file__).resolve().parent.parent
DESTINATION = ROOT / 'reference_output'


def main():
    expected = json.loads((ROOT / 'src' / 'expected.json').read_text(encoding='utf-8'))
    DESTINATION.mkdir(exist_ok=True)
    files = []
    for case, table in expected.items():
        if 'sheets' in table:
            path = DESTINATION / f'{case}.xlsx'
            workbook(path, {
                name: [sheet['columns'], *sheet['rows']]
                for name, sheet in table['sheets'].items()
            })
        elif case == '02-json':
            path = DESTINATION / f'{case}.json'
            records = [dict(zip(table['columns'], row)) for row in table['rows']]
            path.write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
        else:
            path = DESTINATION / f'{case}.csv'
            with path.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream)
                writer.writerow(table['columns'])
                writer.writerows(table['rows'])
        check(case, path)
        files.append(path.name)
    print(f'Generated and checked {len(files)} reference outputs in {DESTINATION}')
    for name in sorted(files):
        print(name)


if __name__ == '__main__':
    main()
