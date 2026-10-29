"""Reference self-check: verify expectations using the supplied runtime."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))
import pipeline_runtime as rt
from check_output import check, compare
from flow_artifacts import REFERENCE_IDS


def main():
    tables = {}
    csv = lambda name: rt.read_csv(str(ROOT / 'data' / name))
    tables['01a'] = csv('people.csv')
    tables['01b'] = csv('headerless.csv')
    tables['02a'] = rt.read_json(str(ROOT / 'data/accounts.json'))
    sheets = rt.read_excel_workbook(str(ROOT / 'data/sources.xlsx'))
    tables['03a'], tables['03b'] = sheets['q1_results'], sheets['q2_results']
    n = csv('numbers.csv')
    for letter, op in zip('abcdef', ['=', '!=', '<', '<=', '>', '>=']):
        tables[f'04{letter}'] = n.filter(f'"value" {op} 10')
    logic = csv('logic.csv')
    tables['05a'] = logic.filter('a = 1 OR b = 1 AND NOT c = 1')
    tables['05b'] = logic.filter('(a = 1 OR b = 1) AND NOT (c = 1)')
    tables['06a'] = n.filter('value IN (-10, 0, 25, 75)')
    cast = rt.read_json(str(ROOT / 'data/cast_values.json'))
    assert cast.schema['amount'] == 'string', cast.schema
    temporary = cast.filter('CAST(amount AS BIGINT) > 7')
    assert temporary.schema['amount'] == 'string'
    tables['06b'] = temporary
    mapped = n.map([rt.FieldMapping('value', 'reading')])
    assert 'value' in n.schema and 'reading' not in n.schema
    tables['07a'] = tables['07b-mapped'] = mapped
    tables['07b-original'] = tables['15a'] = tables['15b'] = n
    persistent = cast.map([rt.FieldMapping('amount', 'amount', 'int')])
    assert persistent.schema['amount'] == 'int'
    tables['08a'] = persistent.filter('amount > 10')
    tables['08b'] = cast.map([rt.FieldMapping('amount', 'quantity', 'int')])
    sales = csv('sales.csv')
    agg = rt.AggregateCall
    tables['09a'] = sales.aggregate(['region'], [agg('SUM', 'amount', 'total'), agg('MIN', 'amount', 'smallest'), agg('MAX', 'amount', 'largest')])
    tables['09b'] = sales.aggregate(['region', 'category'], [agg('SUM', 'amount', 'total')])
    tables['10a'] = sales.aggregate(['region'], [agg('COUNT', '*', 'rows'), agg('COUNT', 'amount', 'present')])
    left, right, third = csv('left.csv'), csv('right.csv'), csv('third.csv')
    pairs = [(left, 'customer_id'), (right, 'account_id')]
    tables['11a'] = tables['12a'] = rt.merge(pairs)
    tables['11b'] = rt.merge(pairs + [(third, 'person_id')])
    tables['12b'] = left
    tables['14a'] = tables['01a'].select(['2024_total', 'full_name'])
    assert csv('dates.csv').schema['joined'] == 'date'
    assert not rt.can_cast('date', 'int')
    bad = csv('bad_cast.csv')
    assert bad.schema['amount'] == 'string' and rt.can_cast('string', 'int')
    try:
        bad.map([rt.FieldMapping('amount', 'amount', 'int')])
    except rt.PipelineRuntimeError:
        pass
    else:
        raise AssertionError('bad_cast fixture must fail on actual values')
    tables = {new: tables[old] for new, old in REFERENCE_IDS.items()}
    tables['06-named'] = tables['06-named'].select(['name', 'balance'])
    tables['06-inline'] = tables['06-inline'].select(['name', 'balance', 'tier'])
    # Evaluate the actual combined flow conditions, not just equivalent references.
    tables['04-range'] = n.filter('value >= -5 AND value <= 20')
    tables['04-outside'] = n.filter('value != 50 AND (value < 0 OR value > 30)')
    tables['05-region'] = sales.aggregate(['region'], [
        agg('SUM', 'amount', 'total'), agg('MIN', 'amount', 'smallest'),
        agg('MAX', 'amount', 'largest'), agg('COUNT', '*', 'rows'),
        agg('COUNT', 'amount', 'present'),
    ])
    with tempfile.TemporaryDirectory() as folder:
        for case, table in tables.items():
            path = Path(folder) / (case + ('.json' if case == '02-json' else '.csv'))
            if case == '02-json':
                table.write_json(str(path))
            else:
                table.write_csv(str(path))
            check(case, path)
        path = Path(folder) / '03-workbook.xlsx'
        rt.write_excel_workbook({'q1_report': sheets['q1_results'], 'q2_report': sheets['q2_results']}, str(path))
        check('03-workbook', path)
    # The comparison must reject lost duplicates and changed column order.
    for actual in [(['x'], [[1]]), (['wrong'], [[1], [1]])]:
        try:
            compare(actual, {'columns': ['x'], 'rows': [[1], [1]]})
        except ValueError:
            pass
        else:
            raise AssertionError('checker accepted a corrupted table')
    print(f'PASS: {len(tables) + 1} output expectations, fixture schemas, cast failure, and checker rejection checks.')


if __name__ == '__main__':
    main()
