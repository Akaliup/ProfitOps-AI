import csv
import io
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.inventory import InventoryPolicy, calculate_inventory
from app.models import DailyOperation, ImportBatch, Order, SourceRecord
from app.weekly import explain_weekly


def upload(client, approver, kind, fields, rows, preview=False):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(fields.split(','))
    writer.writerows(rows)
    return client.post(f'/api/data/{kind}/{"preview" if preview else "commit"}', headers=approver, content=out.getvalue())

ADS = 'store_id,date,sku,campaign_id,ad_spend'
STOCK = 'store_id,snapshot_date,sku,on_hand,reserved,inbound_quantity,inbound_eta,lead_time_days,safety_stock_days,moq,case_pack'
ORDERS = 'store_id,order_id,order_date,sku,quantity,gross_sales,cogs,currency'


def test_source_roundtrip_replay_and_weekly(client, approver):
    # Outside seeded dates, so campaign imports cannot collide with synthetic ledger.
    day = '2020-01-12'
    rows = [['us-store','same',day,'TRAVEL-BAG-01',7,'100.01','20.00','USD'], ['eu-store','same',day,'TRAVEL-BAG-01',3,'60.00','10.00','USD']]
    response = upload(client, approver, 'orders', ORDERS, rows)
    assert response.status_code == 200, response.text
    assert response.json()['accepted_rows'] == 2
    assert upload(client, approver, 'orders', ORDERS, rows).json()['skipped_rows'] == 2
    ads = [['us-store',day,'TRAVEL-BAG-01','a','5.01'], ['us-store',day,'TRAVEL-BAG-01','b','2.00']]
    assert upload(client, approver, 'ads', ADS, ads).json()['accepted_rows'] == 2
    assert upload(client, approver, 'ads', ADS, ads).json()['skipped_rows'] == 2
    stock = [['us-store',day,'TRAVEL-BAG-01',2,1,100,'2020-01-20',7,3,10,6]]
    assert upload(client, approver, 'inventory', STOCK, stock).json()['accepted_rows'] == 1
    result = client.post('/api/weekly-reports', headers=approver, json={'end_date': day}).json()
    assert result['metrics']['current']['sales'] == 16001
    assert result['metrics']['current']['net_profit'] == 12300
    assert result['metrics']['current']['ad_spend'] == 701
    assert len(result['stores']) == 2
    assert result['metrics']['bridge_verified']
    assert sum(d['contribution'] for d in result['metrics']['drivers']) == result['metrics']['changes']['net_profit']['absolute']
    for sku in result['sku_diagnostics']:
        assert sum(sku['bridge'].values()) == sku['profit_delta']
    assert result['inventory']['items'][0]['risk'] == 'INSUFFICIENT_DATA'
    assert len(result['source_rows']) == 5
    assert client.get('/api/weekly-reports/' + result['id'], headers=approver).json()['metrics'] == result['metrics']
    assert client.get('/api/weekly-reports', headers=approver).json()['items'][0]['id'] == result['id']


def test_documented_csv_examples_generate_two_store_report(client, approver):
    examples = Path(__file__).resolve().parents[2] / 'docs' / 'examples' / 'intelligence'
    for kind in ('orders', 'ads', 'inventory'):
        content = (examples / f'{kind}.csv').read_text(encoding='utf-8')
        preview = client.post(f'/api/data/{kind}/preview', headers=approver, content=content)
        assert preview.status_code == 200, preview.text
        assert preview.json()['accepted_rows'] == 2
        assert len(preview.json()['preview_rows']) == 2
        committed = client.post(f'/api/data/{kind}/commit', headers=approver, content=content)
        assert committed.status_code == 200, committed.text
        assert committed.json()['status'] == 'committed'
    report = client.post('/api/weekly-reports', headers=approver, json={'end_date': '2020-01-12'})
    assert report.status_code == 200, report.text
    body = report.json()
    assert {store['store_id'] for store in body['stores']} == {'us-store', 'eu-store'}
    assert body['metrics']['bridge_verified']
    assert len(body['source_rows']) == 6


@pytest.mark.parametrize('index,value', [(2,'not-date'),(5,'NaN'),(5,'1.001'),(5,'-1.00'),(3,'unknown'),(0,'other-tenant'),(7,'EUR'),(6,'')])
def test_invalid_source_atomic_and_history(client, approver, index, value):
    row = ['us-store','invalid','2020-01-12','TRAVEL-BAG-01',1,'10','2','USD']
    row[index] = value
    result = upload(client, approver, 'orders', ORDERS, [row]).json()
    assert result['status'] == 'blocked'
    assert result['accepted_rows'] == 0
    with client.app.state.factory() as session:
        assert session.scalar(select(ImportBatch).where(ImportBatch.id == result['id'])).rejected_rows == 1
        assert not session.scalar(select(SourceRecord))


def test_missing_header_preview_permission_and_mapping(client, approver, analyst):
    assert upload(client, approver, 'orders', 'store_id,sku', [['us-store','TRAVEL-BAG-01']]).status_code == 422
    assert upload(client, analyst, 'ads', ADS, [['us-store','2020-01-12','TRAVEL-BAG-01','a',1]]).status_code == 403
    response = client.post('/api/data/ads/preview', params={'mapping':'{"store_id":"shop"}'}, headers=analyst, content='shop,date,sku,campaign_id,ad_spend\nus-store,2020-01-12,TRAVEL-BAG-01,a,1')
    assert response.json()['accepted_rows'] == 1
    assert response.json()['preview_rows'][0]['store_id'] == 'us-store'
    with client.app.state.factory() as session:
        assert session.scalar(select(SourceRecord)) is None


def stock(**changes):
    return dict(on_hand=20,reserved=0,inbound_quantity=0,inbound_eta=None,lead_time_days=7,safety_stock_days=3,moq=10,case_pack=6, **changes)


def calc(payload=None, units=28, observed=28):
    return calculate_inventory(payload or stock(), units, 28, observed, date(2020,1,12), date(2020,1,12), InventoryPolicy())


def test_inventory_math_boundaries_and_packing():
    assert calc()['coverage_days'] == '20.00'
    assert calc()['reorder_quantity'] == 0
    p = stock(); p['on_hand'] = 6
    result = calc(p)
    assert result['risk'] == 'CRITICAL'
    assert result['reorder_quantity'] == 12  # ceil((17-6)/6)*6
    p['on_hand'] = 7
    assert calc(p)['risk'] == 'HIGH'
    p['on_hand'] = 16
    assert calc(p)['reorder_quantity'] == 12  # MOQ 10 then pack 6
    assert calc(units=0)['coverage_days'] is None
    assert calc(units=0)['reorder_quantity'] == 0
    assert calc(observed=1)['reorder_quantity'] is None
    assert calc(observed=1)['risk'] == 'INSUFFICIENT_DATA'


def test_inbound_cannot_hide_stockout():
    p = stock(); p.update(on_hand=2, inbound_quantity=50, inbound_eta='2020-01-15')
    assert calc(p)['timely_inbound'] == 0
    assert calc(p)['risk'] == 'CRITICAL'
    p['inbound_eta'] = '2020-01-14'
    assert calc(p)['timely_inbound'] == 50
    assert calc(p)['available'] == 2
    assert calc(p)['reorder_quantity'] == 0
    p['inbound_eta'] = '2020-01-11'
    assert calc(p)['timely_inbound'] == 0


class Fake:
    def __init__(self, data): self.data = data
    def generate_json(self, **kwargs): return SimpleNamespace(data=self.data, meta=SimpleNamespace(model='offline-test'))


def test_weekly_llm_validation():
    body = {'metrics': {'data_quality': {'complete': True}}, 'evidence': [{'id':'ev-sales'}]}
    valid = {'claims': [{'explanation':'销售额下降对利润产生负向贡献 仍需核实业务原因','evidence_ids':['ev-sales']}]}
    assert explain_weekly(body, Fake(valid))['status'] == 'llm_success'
    assert explain_weekly(body, None)['reason'] == 'not_configured'
    for explanation, ids in [('利润下降999美元',['ev-sales']),('费用上升',['made-up']),('原因不明',[])]:
        assert explain_weekly(body, Fake({'claims':[{'explanation':explanation,'evidence_ids':ids}]}))['status'] == 'deterministic_fallback'
    class Broken:
        def generate_json(self, **kwargs): raise TimeoutError()
    assert explain_weekly(body, Broken())['status'] == 'deterministic_fallback'
    body['metrics']['data_quality']['complete'] = False
    assert explain_weekly(body, Fake(valid))['reason'] == 'insufficient_data'


def test_weekly_default_week_and_empty(client, approver):
    result = client.post('/api/weekly-reports', headers=approver, json={'end_date':'2001-01-07','use_llm':True}).json()
    assert result['metrics']['current']['sales'] == 0
    assert result['metrics']['changes']['net_profit']['pct'] is None
    assert not result['metrics']['data_quality']['complete']
    assert result['analysis']['status'] == 'deterministic_fallback'
    result = client.post('/api/weekly-reports', headers=approver, json={}).json()
    assert date.fromisoformat(result['period']['end_date']).weekday() == 6


def test_weekly_requires_complete_utc_week(client, approver):
    assert client.post('/api/weekly-reports', headers=approver, json={'end_date': '2020-01-13'}).status_code == 422
    assert client.post('/api/weekly-reports', headers=approver, json={'end_date': '2099-01-04'}).status_code == 422


def test_source_tenant_isolation(client, approver):
    from app.security import Actor
    from app.source_imports import ingest
    from app.weekly import weekly_data, WeeklyRequest
    from app.inventory import inventory_report
    with client.app.state.factory.begin() as session:
        result = ingest(session, 'ads', ADS+'\nus-store,2020-01-12,TRAVEL-BAG-01,a,1', 'private.csv', Actor('u','approver','other'), 'request')
        assert result.status == 'blocked'
        body = weekly_data(session, 'other', WeeklyRequest(end_date=date(2020,1,12)))
        assert not body['source_rows'] and not body['order_ids'] and not body['stores']
        assert not inventory_report(session, 'other', date(2020,1,12))['items']


def test_read_only_intelligence_tools(client):
    from app.tools import ToolContext
    from app.security import Actor
    context = ToolContext(client.app.state.factory, Actor('a','analyst'), 'read')
    for name in ('get_weekly_intelligence','get_inventory_risks'):
        result = client.app.state.registry.invoke(name, {'end_date':'2020-01-12'}, context)
        assert result.ok, result
