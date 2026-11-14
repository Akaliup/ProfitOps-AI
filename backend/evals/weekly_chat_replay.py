"""Synthetic, offline weekly-chat replay through production tools and validation."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.db import Base, build_engine, make_session_factory
from app.llm import LLMResult, LLMToolCall
from app.models import WeeklyReport
from app.security import Actor
from app.tools import build_registry
from app.weekly_chat import answer_question


class OfflineSettings(Settings):
    @property
    def llm_configured(self):
        # Only the in-memory replay provider is constructed; no credential needed.
        return True


class WeeklyReplayProvider:
    model = 'weekly-chat-synthetic-v1'

    def __init__(self, turns):
        self.turns = iter(turns)

    def chat(self, messages, **kwargs):
        turn = next(self.turns)
        return LLMResult(content=turn.get('content', ''), model=self.model, latency_ms=0,
                         usage=None, attempts=1,
                         tool_calls=[LLMToolCall(**call) for call in turn.get('tool_calls', [])])


def run_replay():
    directory = BACKEND / 'evals'
    cases = json.loads((directory / 'weekly_chat_cases.json').read_text(encoding='utf-8'))
    recording = json.loads((directory / 'recordings/weekly_chat_synthetic_v1.json').read_text(encoding='utf-8'))
    settings = OfflineSettings(_env_file=None, llm_api_key='', knowledge_embedding_provider='hashing')
    results = []
    with TemporaryDirectory() as temp:
        engine = build_engine(f'sqlite:///{Path(temp) / "replay.db"}')
        try:
            Base.metadata.create_all(engine)
            factory = make_session_factory(engine)
            with factory.begin() as session:
                session.add(WeeklyReport(id='synthetic-report', tenant_id='demo', body=recording['snapshot']))
            registry = build_registry()
            for case in cases:
                result, trace = answer_question(factory, registry, Actor('offline', 'analyst'), 'replay',
                    'synthetic-report', case['question'], [], WeeklyReplayProvider(recording['turns'][case['id']]), settings)
                passed = result['status'] == case['status'] and result['reason'] == case['reason']
                if result['status'] == 'llm_success':
                    passed = passed and sorted({i for c in result['claims'] for i in c['evidence_ids']}) == case['evidence_ids']
                results.append({'case_id': case['id'], 'passed': passed, 'status': result['status'],
                                'reason': result['reason'], 'tool_calls': trace.get('tool_calls', 0)})
        finally:
            engine.dispose()
    return {'source': 'synthetic; not a live model transcript', 'network_calls': 0,
            'passed': all(r['passed'] for r in results), 'cases': results}


if __name__ == '__main__':
    result = run_replay()
    output = BACKEND / 'evals/reports/weekly-chat-latest.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"weekly chat replay: {sum(r['passed'] for r in result['cases'])}/{len(result['cases'])}; {output}")
    raise SystemExit(0 if result['passed'] else 1)
