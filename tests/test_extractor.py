"""
Offline tests for imea_extractor: no IMEA API calls, no writes outside tmp_path.
Run with: python -m pytest
"""
import http.server
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta

import pandas as pd
import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import imea_extractor as m  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    # The module loads .env on import; point every write at tmp_path and never sync to the shared folder
    monkeypatch.setenv('IMEA_DATASETS_DIR', str(tmp_path))
    monkeypatch.delenv('IMEA_SHARED_DIR', raising=False)
    return tmp_path


@pytest.fixture
def extractor():
    e = m.IMEADirectExtractor({'username': 'test', 'password': 'test'})
    e.access_token = 'test-token'
    return e


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def series_point(date, value, season='26/27', record_id=1):
    return {'Data': date, 'Valor': value, 'SafraDescricao': season, 'Id': str(record_id),
            'UnidadeDescricao': 'Percentual'}


# --- save_dataset -----------------------------------------------------------

def test_first_seen_at_keeps_first_appearance(tmp_path):
    keys = ['date', 'harvest_season']
    # File written before first_seen_at existed
    pd.DataFrame({'date': ['2026-10-16'], 'harvest_season': ['26/27'], 'percentage': [51.86]}) \
        .to_csv(tmp_path / 'X.csv', index=False)

    m.save_dataset(pd.DataFrame({'date': ['2026-10-16', '2026-10-23'], 'harvest_season': ['26/27'] * 2,
                                 'percentage': [51.86, 75.81], 'first_seen_at': ['2026-11-01 19:00:00'] * 2}),
                   'X.csv', merge_on=keys)
    m.save_dataset(pd.DataFrame({'date': ['2026-10-23', '2026-10-30'], 'harvest_season': ['26/27'] * 2,
                                 'percentage': [76.0, 90.0], 'first_seen_at': ['2026-11-02 19:00:00'] * 2}),
                   'X.csv', merge_on=keys)

    saved = pd.read_csv(tmp_path / 'X.csv').set_index('date')
    assert len(saved) == 3
    assert pd.isna(saved.loc['2026-10-16', 'first_seen_at'])            # appearance unknown
    assert saved.loc['2026-10-23', 'percentage'] == 76.0                 # revision wins...
    assert saved.loc['2026-10-23', 'first_seen_at'] == '2026-11-01 19:00:00'  # ...but keeps first sighting
    assert saved.loc['2026-10-30', 'first_seen_at'] == '2026-11-02 19:00:00'


def test_merge_failure_leaves_file_untouched(tmp_path):
    path = tmp_path / 'Y.csv'
    path.write_text('date,harvest_season,percentage\n2026-01-01,25/26,1\n2026-01-08,25/26,2,EXTRA,COLS\n')
    original = path.read_text()

    with pytest.raises(Exception):
        m.save_dataset(pd.DataFrame({'date': ['2026-02-01'], 'harvest_season': ['25/26'], 'percentage': [3]}),
                       'Y.csv', merge_on=['date', 'harvest_season'])
    assert path.read_text() == original


def test_old_schema_is_backed_up_not_merged(tmp_path):
    pd.DataFrame({'Localidade': ['Sorriso'], 'value': [100.0]}).to_csv(tmp_path / 'P.csv', index=False)

    m.save_dataset(pd.DataFrame({'IndicadorFinalId': ['1'], 'Localidade': ['Sorriso'], 'value': [120.0]}),
                   'P.csv', merge_on=['IndicadorFinalId', 'Localidade'])

    assert len(list(tmp_path.glob('P.csv.*.bak'))) == 1
    assert pd.read_csv(tmp_path / 'P.csv')['value'].tolist() == [120.0]


# --- HTTP and CLI ------------------------------------------------------------

def test_adapter_retries_server_errors():
    hits = {'n': 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits['n'] += 1
            self.send_response(503 if hits['n'] < 3 else 200)
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        session = requests.Session()
        adapter = m.TLSAdapter()
        adapter.max_retries.backoff_factor = 0.01
        session.mount('http://', adapter)
        response = session.post(f'http://127.0.0.1:{server.server_port}/', timeout=5)
    finally:
        server.shutdown()
    assert response.status_code == 200
    assert hits['n'] == 3


def test_cli_rejects_unknown_arguments():
    with pytest.raises(SystemExit):
        m.parse_cli_args(['--mdoe', 'full'])


# --- historical series -------------------------------------------------------

def test_window_is_split_when_api_caps_rows(extractor, monkeypatch):
    days = pd.date_range('2026-10-01', '2026-10-31', freq='D')
    windows = []

    def fake_post(url, json=None, **kwargs):
        start, end = pd.Timestamp(json['inicio']), pd.Timestamp(json['fim'])
        windows.append((start, end))
        rows = [series_point(d.strftime('%Y-%m-%dT00:00:00'), float(i), record_id=i)
                for i, d in enumerate(days) if start <= d <= end]
        return FakeResponse(rows[:m.API_ROW_CAP])

    monkeypatch.setattr(extractor.session, 'post', fake_post)
    monkeypatch.setattr(extractor, 'indicators', {'soy_planting': extractor.indicators['soy_planting']})

    df = extractor.extract_historical_series('2026-10-01', '2026-10-31')

    assert len(df) == len(days)  # every day recovered despite the 10-row cap
    assert len(windows) > 1


def test_duplicate_points_keep_latest_revision(extractor, monkeypatch):
    rows = [series_point('2026-10-23T00:00:00', 75.81, record_id=100),
            series_point('2026-10-23T00:00:00', 76.00, record_id=200),
            series_point('2026-10-23T00:00:00', 40.00, season='25/26', record_id=150)]
    monkeypatch.setattr(extractor.session, 'post', lambda url, json=None, **kw: FakeResponse(rows))
    monkeypatch.setattr(extractor, 'indicators', {'soy_planting': extractor.indicators['soy_planting']})

    df = extractor.extract_historical_series('2026-10-01', '2026-10-31')

    by_season = df.set_index('harvest_season')['percentage']
    assert by_season['26/27'] == 76.00   # highest Id wins
    assert by_season['25/26'] == 40.00   # same date, other season: kept, not a duplicate


def test_failed_requests_are_recorded(extractor, monkeypatch):
    monkeypatch.setattr(extractor.session, 'post', lambda url, json=None, **kw: FakeResponse([], 500))
    monkeypatch.setattr(extractor, 'indicators', {'soy_planting': extractor.indicators['soy_planting']})

    extractor.extract_historical_series('2026-10-01', '2026-10-31')

    assert extractor.failures and 'HTTP 500' in extractor.failures[0]


def test_summary_leaves_unreported_activities_empty(extractor):
    detail = pd.DataFrame({
        'date': ['2026-10-30', '2026-10-31'], 'year': [2026] * 2, 'month': [10] * 2, 'crop': ['Soy'] * 2,
        'state': ['Mato Grosso'] * 2, 'harvest_season': ['26/27', '25/26'],
        'activity': ['Planting', 'Commercialization'], 'percentage': [0.0, 95.0],
    })

    summary = extractor.create_percentage_summary(detail).set_index('date')

    assert summary.loc['2026-10-30', 'planted_percentage'] == 0.0    # a real 0% survives
    assert pd.isna(summary.loc['2026-10-30', 'commercialized_percentage'])
    assert pd.isna(summary.loc['2026-10-31', 'planted_percentage'])
    assert pd.isna(summary['harvested_percentage']).all()


# --- coverage report ---------------------------------------------------------

def test_coverage_report_flags_drops_in_cumulative_series(extractor, tmp_path):
    pd.DataFrame({
        'date': ['2024-01-12', '2024-01-19', '2024-01-26', '2024-02-02'], 'crop': ['Soy'] * 4,
        'harvest_season': ['23/24'] * 4, 'percentage': [10.0, 21.5, 12.8, 100.0],
    }).to_csv(tmp_path / 'BR_IMEA_SOY_HARVEST_PERCENTAGE.csv', index=False)

    report = extractor.build_coverage_report()

    row = report.iloc[0]
    assert row['status'] == 'non_monotonic'
    assert row['decrease_dates'] == '2024-01-26'


# --- indicator names ---------------------------------------------------------

def test_indicator_names_only_looks_up_unknown_ids(extractor, tmp_path, monkeypatch):
    stale = (datetime.now() - timedelta(days=m.INDICATOR_NAME_RETRY_DAYS + 1)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')
    pd.DataFrame({
        'IndicadorFinalId': ['1', '2', '3'],
        'indicator_name': ['Preço soja disponível compra', None, None],
        'indicator_group': ['Preço', None, None],
        'looked_up_at': [stale, today, stale],
    }).to_csv(tmp_path / m.INDICATOR_NAMES_FILE, index=False)

    asked = []

    def fake_fetch(indicator_id):
        asked.append(indicator_id)
        return {'3': {'indicator_name': 'Área de milho', 'indicator_group': 'Safra'},
                '4': {}, '5': None}[indicator_id]

    monkeypatch.setattr(extractor, 'fetch_indicator_name', fake_fetch)
    extractor.update_indicator_names(pd.Series(['1', '2', '3', '4', '5', None]))

    # 1 is named, 2 was found empty recently; 3 is an old miss due for a retry; 4 and 5 are new
    assert sorted(asked) == ['3', '4', '5']
    cache = pd.read_csv(tmp_path / m.INDICATOR_NAMES_FILE, dtype={'IndicadorFinalId': str}).set_index('IndicadorFinalId')
    assert cache.loc['3', 'indicator_name'] == 'Área de milho'
    assert pd.isna(cache.loc['4', 'indicator_name'])   # empty indicator: cached as a miss
    assert '5' not in cache.index                      # failed request: not cached, retried next run


def test_sqlite_prices_carry_indicator_names(extractor, tmp_path):
    pd.DataFrame({'IndicadorFinalId': ['708192508838936580', '9'], 'Localidade': ['Sorriso', 'MT'],
                  'value': [120.5, None], 'CadeiaNome': ['Soy'] * 2,
                  'publication_date': ['2026-10-30', None]}).to_csv(tmp_path / 'BR_IMEA_CURRENT_PRICES.csv', index=False)
    pd.DataFrame({'IndicadorFinalId': ['708192508838936580'], 'indicator_name': ['Preço soja disponível compra'],
                  'indicator_group': ['Preço'], 'looked_up_at': ['2026-11-01']}) \
        .to_csv(tmp_path / m.INDICATOR_NAMES_FILE, index=False)

    db = extractor.export_to_sqlite()

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT IndicadorFinalId, indicator_name FROM crop_prices "
                            "WHERE IndicadorFinalId = 708192508838936580").fetchall()
        columns = [r[1] for r in conn.execute("PRAGMA table_info(crop_prices)")]
    assert rows == [('708192508838936580', 'Preço soja disponível compra')]
    assert columns[:2] == ['IndicadorFinalId', 'indicator_name']
