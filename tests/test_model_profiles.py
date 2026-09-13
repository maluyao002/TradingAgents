"""Offline regression coverage for profile isolation, routing and debate limits."""
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.model_profiles import MODEL_PROFILES, apply_model_profile


@pytest.mark.parametrize('name,rounds', [('quick', 1), ('balanced', 2), ('deep', 3)])
def test_profile_rounds_and_independent_configs(name, rounds):
    original = deepcopy(DEFAULT_CONFIG)
    cfg = apply_model_profile(original, name)
    assert cfg['max_debate_rounds'] == cfg['max_risk_discuss_rounds'] == rounds
    cfg['agent_models']['social']['model'] = 'changed'
    assert apply_model_profile(original, name)['agent_models']['social']['model'] != 'changed'
    assert original == DEFAULT_CONFIG


def test_balanced_default_and_rejected_inputs():
    cfg = apply_model_profile(DEFAULT_CONFIG)
    assert cfg['model_profile'] == 'balanced'
    assert cfg['agent_models']['social'] == {'model': 'gpt-5.6-terra', 'reasoning_effort': 'medium'}
    with pytest.raises(ValueError, match='Unknown model profile'):
        apply_model_profile(DEFAULT_CONFIG, 'typo')
    with pytest.raises(ValueError, match='OpenAI'):
        apply_model_profile(dict(DEFAULT_CONFIG, llm_provider='anthropic'))


@pytest.mark.parametrize('rounds', [1, 2, 3])
def test_debates_stop_after_every_participant_has_spoken(rounds):
    logic = ConditionalLogic(rounds, rounds)
    for count in range(1, 2 * rounds + 1):
        speaker = 'Bull' if count % 2 else 'Bear'
        state = {'investment_debate_state': {'count': count, 'current_response': speaker}}
        expected = 'Research Manager' if count == 2 * rounds else ('Bear Researcher' if count % 2 else 'Bull Researcher')
        assert logic.should_continue_debate(state) == expected
    speakers = ['Aggressive', 'Conservative', 'Neutral']
    for count in range(1, 3 * rounds + 1):
        state = {'risk_debate_state': {'count': count, 'latest_speaker': speakers[(count - 1) % 3]}}
        expected = 'Portfolio Manager' if count == 3 * rounds else speakers[count % 3] + ' Analyst'
        assert logic.should_continue_risk_analysis(state) == expected


@pytest.mark.parametrize('profile_name', list(MODEL_PROFILES))
def test_effective_models_reach_every_agent(monkeypatch, tmp_path, profile_name):
    import tradingagents.graph.setup as setup
    import tradingagents.graph.trading_graph as tg
    cfg = apply_model_profile(dict(DEFAULT_CONFIG, data_cache_dir=str(tmp_path / 'cache'),
                                   results_dir=str(tmp_path / 'results')), profile_name)
    # An old global setting must not replace role-specific effort.
    cfg['openai_reasoning_effort'] = 'low'
    clients = []
    def create_client(**kwargs):
        client = MagicMock()
        client.get_llm.return_value = kwargs
        clients.append(kwargs)
        return client
    monkeypatch.setattr(tg, 'create_llm_client', create_client)
    monkeypatch.setattr(tg, 'set_config', lambda config: None)
    monkeypatch.setattr(tg, 'TradingMemoryLog', MagicMock())
    monkeypatch.setattr(tg.TradingAgentsGraph, '_create_tool_nodes', lambda self: {
        key: (lambda state: {}) for key in ('market', 'social', 'news', 'fundamentals')})
    captured = {}
    factories = {'market': 'market_analyst', 'social': 'sentiment_analyst', 'news': 'news_analyst',
                 'fundamentals': 'fundamentals_analyst', 'bull': 'bull_researcher',
                 'bear': 'bear_researcher', 'research_manager': 'research_manager', 'trader': 'trader',
                 'aggressive': 'aggressive_debator', 'conservative': 'conservative_debator',
                 'neutral': 'neutral_debator', 'portfolio_manager': 'portfolio_manager'}
    for role, factory in factories.items():
        def capture(llm, role=role):
            captured[role] = llm
            return lambda state: {}
        monkeypatch.setattr(setup, 'create_' + factory, capture)
    reflection = MagicMock()
    monkeypatch.setattr(tg, 'Reflector', reflection)
    callback = MagicMock()
    graph = tg.TradingAgentsGraph(config=cfg, callbacks=[callback])
    for role, llm in captured.items():
        assert llm['model'] == cfg['agent_models'][role]['model']
        assert llm['reasoning_effort'] == cfg['agent_models'][role]['reasoning_effort']
        assert llm['callbacks'] == [callback]
    assert set(captured) == set(factories)
    assert len(clients) == len({(v['model'], v['reasoning_effort']) for v in cfg['agent_models'].values()})
    reflection.assert_called_once_with(graph.agent_llms['reflection'])
    previous_signature = graph._run_signature('stock')
    graph.config['agent_models']['market']['reasoning_effort'] = 'changed'
    assert graph._run_signature('stock') != previous_signature


@pytest.mark.parametrize('role,quick,balanced,deep', [
    ('market', 'luna/low', 'terra/medium', 'terra/high'),
    ('social', 'luna/low', 'terra/medium', 'terra/high'),
    ('news', 'luna/low', 'terra/medium', 'terra/high'),
    ('fundamentals', 'terra/medium', 'sol/high', 'sol/high'),
    ('bull', 'luna/low', 'terra/medium', 'sol/medium'),
    ('bear', 'luna/low', 'terra/medium', 'sol/medium'),
    ('research_manager', 'terra/medium', 'sol/high', 'astra/high'),
    ('trader', 'terra/medium', 'sol/medium', 'sol/high'),
    ('aggressive', 'luna/low', 'terra/medium', 'sol/medium'),
    ('conservative', 'luna/low', 'terra/medium', 'sol/medium'),
    ('neutral', 'luna/low', 'terra/medium', 'sol/medium'),
    ('portfolio_manager', 'terra/medium', 'astra/high', 'astra/high'),
])
def test_agreed_profile_assignments(role, quick, balanced, deep):
    for name, expected in zip(('quick', 'balanced', 'deep'), (quick, balanced, deep), strict=True):
        setting = MODEL_PROFILES[name]['agents'][role]
        tier, effort = expected.split('/')
        assert setting['model'] == ('gpt-6-astra' if tier == 'astra' else 'gpt-5.6-' + tier)
        assert setting['reasoning_effort'] == effort
