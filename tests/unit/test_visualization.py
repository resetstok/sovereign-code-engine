from src.visualization import handle_window_click
import pytest

def test_handle_window_click(monkeypatch):
    monkeypatch.setattr('src.visualization.start_animation', lambda: None)
    handle_window_click(None)