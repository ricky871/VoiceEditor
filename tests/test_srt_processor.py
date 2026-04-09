import pytest
from pathlib import Path
from src.tts.processor import SRTProcessor

def test_srt_parsing(tmp_path):
    # Create a dummy SRT file
    srt_content = """1
00:00:01,000 --> 00:00:03,000
你好，世界。

2
00:00:04,500 --> 00:00:06,000
Hello World.
"""
    srt_file = tmp_path / "test.srt"
    srt_file.write_text(srt_content, encoding="utf-8")
    
    entries = SRTProcessor.parse(srt_file)
    
    assert len(entries) == 2
    assert entries[0]["text"] == "你好，世界。"
    assert entries[0]["start_ms"] == 1000
    assert entries[0]["end_ms"] == 3000
    assert entries[0]["dur_ms"] == 2000
    
    assert entries[1]["text"] == "Hello World."
    assert entries[1]["start_ms"] == 4500
    assert entries[1]["end_ms"] == 6000
    assert entries[1]["dur_ms"] == 1500

def test_srt_resolve_path(tmp_path):
    srt_file = tmp_path / "actual.srt"
    srt_file.write_text("dummy", encoding="utf-8")
    
    # Test direct path
    assert SRTProcessor.resolve_path(str(srt_file)) == srt_file
    
    # Test wildcard (glob)
    wildcard = str(tmp_path / "*.srt")
    assert SRTProcessor.resolve_path(wildcard) == srt_file
    
    # Test missing
    with pytest.raises(FileNotFoundError):
        SRTProcessor.resolve_path(str(tmp_path / "nonexistent.srt"))
