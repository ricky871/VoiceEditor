import pytest
import json
import logging
from pathlib import Path
from ui.state import AppState

def test_app_state_to_from_dict():
    state = AppState()
    state.url_or_path = "https://example.com/video.mp4"
    state.work_dir = "custom_work"
    state.lang = "en"
    state.logs = ["Processing start", "Success"]
    
    data = state.to_dict()
    assert data["url_or_path"] == "https://example.com/video.mp4"
    assert data["work_dir"] == "custom_work"
    assert data["lang"] == "en"
    assert "logs" in data
    assert data["logs"] == ["Processing start", "Success"]
    
    # Restore to a new state object
    new_state = AppState()
    new_state.from_dict(data)
    
    assert new_state.url_or_path == "https://example.com/video.mp4"
    assert new_state.work_dir == "custom_work"
    assert new_state.lang == "en"
    assert new_state.logs == ["Processing start", "Success"]

def test_app_state_undo_history():
    state = AppState()
    state.srt_entries = [{"id": 1, "text": "Original"}]
    
    # First modification
    state.push_srt_history()
    state.srt_entries = [{"id": 1, "text": "Modified"}]
    
    # Undo
    assert state.undo_srt_change() is True
    assert state.srt_entries[0]["text"] == "Original"
    
    # Undo when no history
    assert state.undo_srt_change() is False

def test_progress_inference():
    state = AppState()
    
    # Test step 1 inference
    state.append_log(">> 开始处理视频")
    assert state.step == 1
    assert state.progress >= 0.05
    
    # Test step 2 inference (relying on cache hit log)
    state.append_log(">> 音色提取成功")
    assert state.step == 2
    assert state.progress >= 0.60
    
    # Test step 3 inference (segment log)
    state.append_log("Processing Segment 1/10")
    assert state.step == 3
    assert state.segment_current == 1
    assert state.segment_total == 10
    assert state.progress >= 0.70
    
    # Test step 4 inference
    state.append_log(">> TTS 生成成功")
    assert state.step == 4
    assert state.progress == 1.0


def test_app_state_version_and_missing_paths(tmp_path):
    state = AppState()
    legacy_data = {
        "url_or_path": "https://example.com/video.mp4",
        "work_dir": str(tmp_path / "work"),
        "srt_path": str(tmp_path / "missing.srt"),
        "video_data": {
            "video": str(tmp_path / "missing.mp4"),
            "audio": str(tmp_path / "missing.wav"),
            "srt": str(tmp_path / "missing.srt"),
            "voice_ref": str(tmp_path / "missing_ref.wav"),
        },
    }

    state.from_dict(legacy_data)
    # Version should be upgraded to current version (2) due to migration
    assert state.state_version == 2
    # But fields should still be restored before clearing
    assert state.srt_path == tmp_path / "missing.srt"

    cleared = state.clear_invalid_paths()
    assert len(cleared) > 0  # Should have cleared some fields
    assert state.srt_path is None
    assert state.video_data is None
    assert state.srt_entries == []
    assert state.step == 1
    assert state.progress == 0.0


def test_append_log_inferrs_progress_outside_lock(monkeypatch):
    state = AppState()
    observed = {}

    def fake_infer(text):
        observed["text"] = text
        observed["lock_held"] = state._lock.locked()

    monkeypatch.setattr(state, "_infer_progress_from_log", fake_infer)

    state.append_log("Processing Segment 2/5")

    assert observed == {"text": "Processing Segment 2/5", "lock_held": False}


def test_clear_invalid_paths_logs_outside_lock(tmp_path, monkeypatch):
    state = AppState()
    state.srt_path = tmp_path / "missing.srt"

    warnings = []

    def fake_warning(message):
        acquired = state._lock.acquire(blocking=False)
        assert acquired, "logging.warning was called while state lock was held"
        state._lock.release()
        warnings.append(message)

    monkeypatch.setattr(logging, "warning", fake_warning)

    cleared = state.clear_invalid_paths()

    assert cleared == ["srt_path"]
    assert warnings == [f"SRT file no longer exists: {tmp_path / 'missing.srt'}"]
