"""Render real Rich frames without model calls or a live terminal."""
from io import StringIO

import pytest
from rich.console import Console

import cli.main as main


@pytest.fixture
def buffer(monkeypatch):
    buffer = main.MessageBuffer()
    buffer.init_for_analysis(['market', 'social', 'news', 'fundamentals'])
    monkeypatch.setattr(main, 'message_buffer', buffer)
    return buffer


def render(dashboard, width=80, height=24):
    output = StringIO()
    Console(file=output, width=width, height=height, color_system=None).print(dashboard)
    return output.getvalue()


@pytest.mark.parametrize('width,height', [(80, 24), (60, 24), (48, 20), (120, 40)])
def test_active_agent_and_activity_visible_in_small_terminals(buffer, width, height):
    # The last agent must be visible even when there isn't room for the full roster.
    for agent in buffer.agent_status:
        buffer.update_agent_status(agent, 'completed')
    buffer.update_agent_status('Portfolio Manager', 'in_progress')
    buffer.add_tool_call('get_stock_data', {'symbol': 'SPY'})
    frame = render(main.AnalysisDashboard(main.create_layout()), width, height)
    assert 'Portfolio Manager' in frame
    assert 'Running' in frame
    assert 'get_stock_data' in frame
    assert 'Messages & Tools' in frame


def test_latest_activity_wins_even_with_identical_timestamps(buffer):
    for i in range(30):
        buffer.add_message('System', f'Old entry {i}')
    buffer.add_tool_call('get_stock_data', {})
    buffer.add_message('Analysis', 'Latest finding')
    # Force all timestamps to be equal, reproducing fast bursts of updates.
    buffer.activity = type(buffer.activity)(
        [('12:00:00', kind, text) for _, kind, text in buffer.activity], maxlen=100
    )
    frame = render(main.AnalysisDashboard(main.create_layout()))
    assert frame.index('Latest finding') < frame.index('get_stock_data')
    assert 'Old entry 0 ' not in frame


def test_resize_and_timer_refresh_without_new_graph_chunk(buffer, monkeypatch):
    buffer.update_agent_status('Market Analyst', 'in_progress')
    buffer.add_message('System', 'Waiting for model')
    clock = [105.0]
    monkeypatch.setattr(main.time, 'time', lambda: clock[0])
    dashboard = main.AnalysisDashboard(main.create_layout(), start_time=100.0)
    assert '00:05' in render(dashboard, 120, 40)
    clock[0] = 111.0
    small = render(dashboard, 60, 24)
    assert '00:11' in small
    assert 'Market Analyst' in small
    assert 'Waiting for model' in small
    wide = render(dashboard, 100, 30)
    assert 'Market Analyst' in wide
    assert 'Waiting for model' in wide


def test_empty_and_completed_states_are_explicit(buffer):
    dashboard = main.AnalysisDashboard(main.create_layout())
    assert 'Waiting for agent activity' in render(dashboard)
    for agent in buffer.agent_status:
        buffer.update_agent_status(agent, 'completed')
    assert 'Analysis complete' in render(dashboard)
    buffer.add_message('System', 'Old run')
    buffer.init_for_analysis(['market'])
    assert not buffer.activity
    assert 'Old run' not in render(dashboard)
