"""Exercise the runner independently of any PipelineQL implementation."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_tests import score_flow

MANIFEST = json.loads((HERE / 'automation.json').read_text(encoding='utf-8'))
EXPECTED = json.loads((HERE / 'expected.json').read_text(encoding='utf-8'))


@pytest.fixture
def implementation(tmp_path):
    root = tmp_path / 'implementation'
    ql = root / 'grading' / 'ql'
    ql.mkdir(parents=True)
    (root / 'pipelineql.lark').write_text('start: CODE\nCODE: /f[0-9]+/\n', encoding='utf-8')
    for number in range(1, 11):
        (ql / f'flow{number:02}.ql').write_text('!' if number == 7 else f'f{number}', encoding='utf-8')
    # Deliberate predetermined outputs: this fixture tests the harness, not a DSL.
    source = '''
import csv
import json
from pathlib import Path
import openpyxl
import pipeline_runtime as rt
from pipeline_errors import StructuralError, SchemaError, ValidationErrors, ExecutionError
EXPECTED = json.loads(EXPECTED_LITERAL)
FILES = json.loads(FILES_LITERAL)
def validate(tree):
    n = int(str(tree.children[0])[1:])
    if n == 8:
        raise ValidationErrors([StructuralError('unknown ' + name) for name in ['missing_customers','missing_orders','missing_products']])
    if n == 9:
        raise ValidationErrors([SchemaError('invalid ' + name) for name in ['missing_field','joined','name']])
def execute(tree):
    n = int(str(tree.children[0])[1:])
    if n == 10:
        try:
            raise rt.PipelineRuntimeError('bad value')
        except rt.PipelineRuntimeError as error:
            raise ExecutionError(str(error)) from error
    for key, filename in FILES.get(str(n), {}).items():
        table = EXPECTED[key]
        path = Path('grading/output') / filename
        if 'sheets' in table:
            book = openpyxl.Workbook()
            book.remove(book.active)
            for name, content in table['sheets'].items():
                sheet = book.create_sheet(name)
                sheet.append(content['columns'])
                for row in content['rows']:
                    sheet.append(row)
            book.save(path)
        elif path.suffix == '.json':
            path.write_text(json.dumps([dict(zip(table['columns'], row)) for row in table['rows']]))
        else:
            with path.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(table['columns'])
                writer.writerows(table['rows'])
'''
    files = {str(i): {c['id']: c['file'] for c in f['checks'] if 'id' in c} for i, f in enumerate(MANIFEST['flows'], 1)}
    source = source.replace('EXPECTED_LITERAL', repr(json.dumps(EXPECTED))).replace('FILES_LITERAL', repr(json.dumps(files)))
    (root / 'interpreter.py').write_text(source, encoding='utf-8')
    return root


def invoke(root, results, *extra):
    process = subprocess.run([sys.executable, str(HERE / 'run_tests.py'), '--implementation', str(root),
                              '--results', str(results), *extra], capture_output=True, text=True, timeout=120)
    reports = list(results.glob('*/report.json'))
    assert reports, process.stdout + process.stderr
    return process, json.loads(reports[0].read_text(encoding='utf-8'))


def test_full_runner_twenty_points(implementation, tmp_path):
    process, report = invoke(implementation, tmp_path / 'results')
    assert process.returncode == 0, process.stdout + process.stderr
    assert report['earned'] == 20
    assert report['blocked'] == 0
    assert len(report['flows']) == 10
    assert report['score_matrix'][-1] == {'flow': 'Total', 'value': 20, 'score': 20}
    saved = next((tmp_path / 'results').iterdir())
    import csv
    with (saved / 'scores.csv').open(newline='') as stream:
        matrix = list(csv.DictReader(stream))
    assert len(matrix) == 11
    assert matrix[0] == {'flow': 'flow01', 'value': '2', 'score': '2.00'}
    assert matrix[-1] == {'flow': 'Total', 'value': '20', 'score': '20.00'}
    assert 'Total' in (saved / 'summary.txt').read_text()
    assert not (implementation / 'grading' / 'output').exists()


def test_duplicate_diagnostics_do_not_pass(implementation, tmp_path):
    path = implementation / 'interpreter.py'
    path.write_text(path.read_text().replace("['missing_customers','missing_orders','missing_products']",
                                             "['missing_customers','missing_customers','missing_customers']"))
    process, report = invoke(implementation, tmp_path / 'results', '--flow', '8')
    assert process.returncode == 2
    assert report['earned'] == 0.5
    assert report['blocked'] == 1
    assert report['score_matrix'][-1] == {'flow': 'Total', 'value': 2, 'score': 0.5}
    assert report['flows'][0]['checks'][-1]['status'] == 'FAIL'


def test_first_error_only_has_single_collection_loss(implementation, tmp_path):
    path = implementation / 'interpreter.py'
    source = path.read_text().replace("['missing_customers','missing_orders','missing_products']", "['missing_customers']")
    source = source.replace("['missing_field','joined','name']", "['missing_field']")
    path.write_text(source)
    process, report = invoke(implementation, tmp_path / 'results', '--flow', '8', '9')
    failures = [c for f in report['flows'] for c in f['checks'] if c['status'] == 'FAIL']
    assert len(failures) == 1
    assert failures[0]['points'] == '1/2'
    assert report['blocked'] == 2
    assert report['earned'] == 1.5


def test_raw_runtime_error_is_not_correct_wrapping(implementation, tmp_path):
    path = implementation / 'interpreter.py'
    path.write_text(path.read_text().replace('raise ExecutionError(str(error)) from error', 'raise error'))
    process, report = invoke(implementation, tmp_path / 'results', '--flow', '10')
    assert process.returncode == 1
    assert report['earned'] == 1


def test_missing_program_is_failure(implementation, tmp_path):
    (implementation / 'grading/ql/flow01.ql').unlink()
    process, report = invoke(implementation, tmp_path / 'results', '--flow', '1')
    assert process.returncode == 1
    assert report['earned'] == 0


def test_setup_failure_is_blocked(implementation, tmp_path):
    (implementation / 'pipelineql.lark').write_text('not a grammar')
    process, report = invoke(implementation, tmp_path / 'results', '--flow', '1')
    assert process.returncode == 2
    assert report['blocked'] == 2


def test_projection_partial_credit(tmp_path):
    table = EXPECTED['05-region']
    import csv
    with (tmp_path / '05-region.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(table['columns'])
        for row in table['rows']:
            changed = list(row)
            changed[1] = -999
            writer.writerow(changed)
    category = EXPECTED['05-category']
    with (tmp_path / '05-category.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(category['columns'])
        writer.writerows(category['rows'])
    checks = score_flow(MANIFEST['flows'][4], {'exit_code': 0}, tmp_path, EXPECTED)
    assert [c['status'] for c in checks] == ['FAIL', 'PASS', 'PASS', 'PASS', 'PASS', 'PASS']


def test_negative_output_prevents_full_credit(tmp_path):
    (tmp_path / 'unexpected.csv').write_text('id\n1\n')
    observed = {'parsed': True, 'exit_code': 1, 'validation_error': {'type': 'ValidationErrors',
                'errors': [{'type': 'SchemaError', 'message': name} for name in ['missing_field', 'joined', 'name']]}}
    checks = score_flow(MANIFEST['flows'][8], observed, tmp_path, EXPECTED)
    assert checks[-1]['status'] == 'FAIL'


def test_timeout_is_blocked(implementation, tmp_path):
    path = implementation / 'interpreter.py'
    path.write_text('import time\ntime.sleep(5)\n' + path.read_text())
    process, report = invoke(implementation, tmp_path / 'results', '--flow', '1', '--timeout', '0.2')
    assert process.returncode == 2
    assert report['blocked'] == 2


def test_wrong_validation_tier_fails(tmp_path):
    observed = {'parsed': True, 'exit_code': 1, 'validation_error': {'type': 'ValidationErrors',
                'errors': [{'type': 'SchemaError', 'message': name} for name in
                           ['missing_customers', 'missing_orders', 'missing_products']]}}
    checks = score_flow(MANIFEST['flows'][7], observed, tmp_path, EXPECTED)
    assert all(c['status'] == 'FAIL' for c in checks)


def test_structural_errors_cannot_create_output(tmp_path):
    (tmp_path / 'unexpected.csv').write_text('id\n1\n')
    observed = {'parsed': True, 'exit_code': 1, 'validation_error': {'type': 'ValidationErrors',
                'errors': [{'type': 'StructuralError', 'message': name} for name in
                           ['missing_customers', 'missing_orders', 'missing_products']]}}
    checks = score_flow(MANIFEST['flows'][7], observed, tmp_path, EXPECTED)
    assert checks[-1]['status'] == 'FAIL'
