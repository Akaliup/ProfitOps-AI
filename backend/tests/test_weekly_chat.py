import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select, text, update

from app.errors import DomainError
from app.llm import LLMResult, LLMToolCall
from app.llm_tools import LLM_TOOL_ALLOWLIST, build_tool_definitions
from app.models import AuditLog, WeeklyChatMessage, WeeklyChatThread, WeeklyReport
from app.security import Actor
from app.tools import ToolContext
from app.weekly_chat import (WeeklyChatAnswer, assert_numbers_supported, get_messages,
                            post_message, verify_answer, extract_allowed_numbers)
from app.weekly_snapshot import snapshot_view


@pytest.fixture
def imported_examples(client, approver):
    examples = Path(__file__).resolve().parents[2] / 'docs/examples/intelligence'
    for kind in ('orders', 'ads', 'inventory'):
        r = client.post(f'/api/data/{kind}/commit', headers=approver,
                        content=(examples / f'{kind}.csv').read_text(encoding='utf-8'))
        assert r.status_code == 200


@pytest.fixture
def report(client, approver, imported_examples):
    response = client.post('/api/weekly-reports', headers=approver, json={'end_date': '2020-01-12', 'store_id': 'us-store'})
    assert response.status_code == 200
    return response.json()


class FakeChat:
    model = 'synthetic-weekly-test'

    def __init__(self, report_id, answer, tool='get_saved_weekly_report'):
        self.report_id, self.answer, self.tool = report_id, answer, tool
        self.calls = 0
        self.tool_results = []

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.tool_results.extend(json.loads(message['content']) for message in messages
                                 if message['role'] == 'tool')
        assert [t['function']['name'] for t in kwargs['tools']] == ['get_saved_weekly_report']
        calls = [LLMToolCall('read-snapshot', self.tool, json.dumps({'report_id': self.report_id}))] if self.calls == 1 and self.tool else []
        return LLMResult(content='' if calls else json.dumps(self.answer), model=self.model,
                         latency_ms=0, usage=None, attempts=1, tool_calls=calls)


class BrokenChat:
    model = 'synthetic-weekly-broken'

    def chat(self, messages, **kwargs):
        raise RuntimeError('synthetic provider outage')


def answer(explanation='销售额见证据', ids=None, summary='销售额见证据'):
    return {'status': 'llm_success', 'summary': summary, 'claims': [
        {'explanation': explanation, 'evidence_ids': ids or ['ev-sales']}], 'refusal': None}


def enable_fake(client, report, data, tool='get_saved_weekly_report', report_id=None):
    # Synthetic credential is never used by a network provider.
    from pydantic import SecretStr
    client.app.state.settings.llm_api_key = SecretStr('offline-fake-only')
    client.app.state.settings.llm_provider = 'deepseek'
    client.app.state.llm = FakeChat(report_id or report['id'], data, tool)


def ask(client, headers, report, question='销售额是多少？'):
    return client.post(f"/api/weekly-reports/{report['id']}/messages", headers=headers, json={'question': question})


def test_no_key_history_audit_and_immutable_snapshot(client, analyst, report):
    path = f"/api/weekly-reports/{report['id']}/messages"
    with client.app.state.factory() as session:
        report_count = session.scalar(select(func.count()).select_from(WeeklyReport))
    assert client.get(path, headers=analyst).json() == {'thread': None, 'items': []}
    result = ask(client, analyst, report)
    assert result.status_code == 200
    assert result.json()['reason'] == 'not_configured'
    assert result.json()['claims'] == []
    history = client.get(path, headers=analyst).json()
    assert [m['role'] for m in history['items']] == ['user', 'assistant']
    assert all('trace' not in m and m['created_at'].endswith('+00:00') for m in history['items'])
    assert client.get(f"/api/weekly-reports/{report['id']}", headers=analyst).json() == report
    with client.app.state.factory() as session:
        log = session.scalar(select(AuditLog).where(AuditLog.action == 'weekly.chat'))
        assert log.object_id == report['id'] and log.details['status'] == 'deterministic_fallback'
        assert session.scalar(select(func.count()).select_from(WeeklyReport)) == report_count


def test_provider_exception_is_an_explicit_fallback(client, analyst, report):
    from pydantic import SecretStr
    client.app.state.settings.llm_api_key = SecretStr('offline-fake-only')
    client.app.state.settings.llm_provider = 'deepseek'
    client.app.state.llm = BrokenChat()
    response = ask(client, analyst, report)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['status'], body['reason'], body['claims']) == (
        'deterministic_fallback', 'llm_unknown_error', []
    )
    assert 'synthetic provider outage' not in body['content']
    history = client.get(f"/api/weekly-reports/{report['id']}/messages", headers=analyst).json()
    assert [item['role'] for item in history['items']] == ['user', 'assistant']
    assert history['items'][-1]['reason'] == 'llm_unknown_error'


@pytest.mark.parametrize('data,tool,expected,reason', [
    (answer(), 'get_saved_weekly_report', 'llm_success', None),
    (answer(ids=['made-up']), 'get_saved_weekly_report', 'deterministic_fallback', 'unknown_evidence'),
    (answer('销售额为99999美元'), 'get_saved_weekly_report', 'deterministic_fallback', 'unverified_number'),
    (answer(summary='总额999.99美元'), 'get_saved_weekly_report', 'deterministic_fallback', 'unverified_number'),
    (answer(), None, 'deterministic_fallback', 'invalid_llm_output'),
    (answer(), 'simulate_execute_approved_action', 'deterministic_fallback', 'llm_tool_forbidden'),
    ({'status': 'refused', 'summary': '数据不足', 'claims': [], 'refusal': '数据不足'}, 'get_saved_weekly_report', 'refused', 'out_of_scope'),
    ({'status': 'llm_success', 'summary': '无引用', 'claims': []}, 'get_saved_weekly_report', 'deterministic_fallback', 'invalid_llm_output'),
])
def test_fake_loop(client, analyst, report, data, tool, expected, reason):
    enable_fake(client, report, data, tool)
    result = ask(client, analyst, report)
    assert result.status_code == 200, result.text
    body = result.json()
    assert (body['status'], body['reason']) == (expected, reason), body
    assert body['claims'] == (data['claims'] if expected == 'llm_success' else [])


def test_scope_and_execution_refusal(client, analyst, report):
    enable_fake(client, report, answer(), report_id='other-report')
    assert ask(client, analyst, report).json()['reason'] == 'llm_tool_scope_mismatch'
    assert ask(client, analyst, report, '帮我把预算改成100').json()['status'] == 'refused'


def test_tenant_and_missing_report(client, analyst, report):
    factory = client.app.state.factory
    actor = Actor('other', 'analyst', 'other')
    for id_ in (report['id'], 'missing'):
        with pytest.raises(DomainError) as error:
            get_messages(factory, actor, id_)
        assert error.value.code == 'report_not_found' and error.value.status == 404
        with pytest.raises(DomainError):
            post_message(factory, client.app.state.registry, actor, 'test', id_, 'question', None, client.app.state.settings)
    assert client.get('/api/weekly-reports/missing/messages', headers=analyst).status_code == 404
    assert client.post('/api/weekly-reports/missing/messages', headers=analyst, json={'question': '问题'}).status_code == 404


def test_validation_cap_and_concurrent_writes(client, analyst, report):
    path = f"/api/weekly-reports/{report['id']}/messages"
    for payload in ({'question': ' '}, {'question': 'x' * 1001}, {'question': 'q', 'store_id': 'us-store'}):
        assert client.post(path, headers=analyst, json=payload).status_code == 422
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ask(client, analyst, report).status_code, range(12)))
    assert results.count(200) == 10 and results.count(422) == 2
    assert ask(client, analyst, report).json()['error']['code'] == 'weekly_chat_limit'
    with client.app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(WeeklyChatThread)) == 1
        assert session.scalar(select(func.count()).select_from(WeeklyChatMessage)) == 20


def test_snapshot_is_read_only_and_default_allowlist_unchanged(client, report):
    state = client.app.state
    with state.factory() as session:
        row = session.get(WeeklyReport, report['id'])
        before = copy.deepcopy(row.body)
        view = snapshot_view(row)
        sizes = {k: len(json.dumps(v, ensure_ascii=False, separators=(',', ':')).encode()) for k, v in view.model_dump(mode='json').items()}
        assert sum(sizes.values()) < 16384, sizes
        assert row.body == before
        assert snapshot_view(row) == view
    context = ToolContext(state.factory, Actor('a', 'analyst'), 'test', report_id=report['id'])
    assert state.registry.invoke('get_saved_weekly_report', {'report_id': report['id']}, context).ok
    assert tuple(t['function']['name'] for t in build_tool_definitions(state.registry)) == LLM_TOOL_ALLOWLIST
    assert 'get_saved_weekly_report' not in LLM_TOOL_ALLOWLIST
    ids = {e['id'] for e in view.citable_evidence}
    assert {'us-store:store', 'us-store:sku:TRAVEL-BAG-01'} <= ids


def test_number_verifier():
    evidence = [{'id': 'inventory-abc', 'value': {'sku': 'TRAVEL-BAG-01', 'sales': 16001,
                 'profit_delta': -1200, 'zero': 0, 'daily_sales': '12.34', 'date': '2020-01-12'}}]
    for text in ('TRAVEL-BAG-01 库存', 'TRAVEL-BAG-01的库存', '2020-01-12的快照',
                 '金额160.01美元', '-12.00美元', '0美元', '12.34%', '2020-01-12'):
        assert_numbers_supported(text, evidence)
    for text in ('999.99美元', '一百美元', 'TRAVEL-BAG-01999', '2021-01-12', '160.01e99'):
        with pytest.raises(ValueError):
            assert_numbers_supported(text, evidence)
    refusal = WeeklyChatAnswer(status='refused', summary='999', refusal='999', claims=[])
    assert '999' not in verify_answer(refusal, [])['content']


def test_non_currency_integers_never_generate_dollar_values():
    evidence = [{'id': 'inventory-abc', 'value': {'on_hand': 2, 'lead_time_days': 7,
                 'reorder_quantity': 12, 'moq': 3, 'case_pack': 6, 'risk_skus': 4,
                 'current': {'units': 9, 'sales': 16001}}}]
    allowed, _ = extract_allowed_numbers(evidence)
    assert {'2', '7', '12', '16001', '160.01'} <= allowed
    for value in ('0.02', '0.07', '0.12', '0.03', '0.06', '0.04', '0.09'):
        assert value not in allowed
        result = verify_answer(WeeklyChatAnswer.model_validate(answer(
            f'销售额{value}美元', ids=['inventory-abc'])), evidence)
        assert result['reason'] == 'unverified_number'
    for content in ('销售额16001美分', '销售额160.01美元'):
        assert_numbers_supported(content, [{'id': 'ev-sales', 'current': 16001}])
    assert_numbers_supported('贡献-12.34美元', [{'bridge': {'ad_spend': -1234}}])


def test_official_dual_store_snapshot_fits_and_chat_succeeds(client, approver, analyst, imported_examples):
    response = client.post('/api/weekly-reports', headers=approver, json={'end_date': '2020-01-12'})
    assert response.status_code == 200
    saved = response.json()
    assert len(saved['stores']) == 2
    with client.app.state.factory() as session:
        row = session.get(WeeklyReport, saved['id'])
        before = copy.deepcopy(row.body)
        view = snapshot_view(row)
        payload = json.dumps({'ok': True, 'data': view.model_dump(mode='json')},
                             ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        assert len(payload) < 16384
        assert b'order_ids' not in payload
        assert row.body == before
        assert {item['id'] for item in before['evidence']} <= {item['id'] for item in view.citable_evidence}
        by_id = {item['id']: item for item in view.citable_evidence}
        for original in before['evidence']:
            compact = by_id[original['id']]
            if 'value' in original:
                for key, value in compact['value'].items():
                    assert value == original['value'][key]
            else:
                for key in ('source', 'current', 'previous', 'contribution', 'period'):
                    assert compact[key] == original[key]
        for row in before['sku_diagnostics']:
            assert by_id[f"{row['store_id']}:sku:{row['sku']}"]['value']['bridge'] == row['bridge']
    data = answer('销售额见证据', ids=['ev-sales', 'us-store:store', 'eu-store:store'])
    enable_fake(client, saved, data)
    result = ask(client, analyst, saved)
    assert result.status_code == 200
    assert result.json()['status'] == 'llm_success', result.json()
    sent = client.app.state.llm.tool_results[-1]
    assert sent['data']['untrusted_data'] is True
    assert sent['data']['notice']
    assert len(json.dumps(sent, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) < 16384
    assert sent['data']['citable_evidence'] == view.citable_evidence
    assert client.get(f"/api/weekly-reports/{saved['id']}", headers=analyst).json() == saved


def test_model_call_allows_independent_database_writer(client, analyst, report, monkeypatch):
    enable_fake(client, report, answer())
    provider = client.app.state.llm
    original = provider.chat

    def chat(messages, **kwargs):
        with client.app.state.factory.begin() as session:
            session.execute(text('PRAGMA busy_timeout=200'))
            thread = session.scalar(select(WeeklyChatThread))
            assert thread is not None
            assert session.scalar(select(func.count()).select_from(WeeklyChatMessage)) == 0
            session.execute(update(WeeklyChatThread).where(WeeklyChatThread.id == thread.id)
                            .values(created_by=thread.created_by))
        return original(messages, **kwargs)

    monkeypatch.setattr(provider, 'chat', chat)
    result = ask(client, analyst, report)
    assert result.status_code == 200
    assert result.json()['status'] == 'llm_success', result.json()


def test_concurrent_model_results_recheck_cap_before_persistence(client, analyst, report, monkeypatch):
    barrier = Barrier(12)

    def synchronized_answer(*args, **kwargs):
        barrier.wait(timeout=20)
        return {'status': 'deterministic_fallback', 'reason': 'not_configured',
                'claims': [], 'content': '模型未配置'}, {}

    monkeypatch.setattr('app.weekly_chat.answer_question', synchronized_answer)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: ask(client, analyst, report), range(12)))
    assert sum(result.status_code == 200 for result in results) == 10
    rejected = [result for result in results if result.status_code == 422]
    assert len(rejected) == 2
    assert all(result.json()['error']['code'] == 'weekly_chat_limit' for result in rejected)
    with client.app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(WeeklyChatThread)) == 1
        assert session.scalar(select(func.count()).select_from(WeeklyChatMessage)) == 20
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == 'weekly.chat')) == 10


def test_oversized_evidence_fails_closed(client, analyst, report):
    enable_fake(client, report, answer())
    client.app.state.settings.llm_tool_result_max_bytes = 1024
    assert ask(client, analyst, report).json()['reason'] == 'llm_tool_result_too_large'


def test_weekly_chat_offline_replay():
    from evals.weekly_chat_replay import run_replay
    result = run_replay()
    assert result['passed'], result
    assert len(result['cases']) >= 8 and result['network_calls'] == 0
