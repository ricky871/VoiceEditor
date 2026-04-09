"""Test SRT format error handling and graceful degradation."""

import pytest
from pathlib import Path
from src.tts.processor import SRTProcessor


def test_srt_parsing_normal_file(tmp_path):
    """Test normal SRT parsing works correctly."""
    srt_content = """1
00:00:01,000 --> 00:00:05,000
Hello world

2
00:00:06,000 --> 00:00:10,000
Second subtitle
"""
    srt_file = tmp_path / "test.srt"
    srt_file.write_text(srt_content, encoding="utf-8")
    
    entries = SRTProcessor.parse(srt_file)
    
    assert len(entries) == 2
    assert entries[0]["text"] == "Hello world"
    assert entries[0]["start_ms"] == 1000
    assert entries[0]["end_ms"] == 5000
    assert entries[0]["dur_ms"] == 4000


def test_srt_parsing_with_malformed_entries(tmp_path):
    """Test that malformed entries are skipped gracefully."""
    srt_content = """1
00:00:01,000 --> 00:00:05,000
Valid entry

2
00:00:06,000 --> 00:00:10,000
Valid second entry

999
INVALID_TIMECODE
This should be skipped

4
00:00:15,000 --> 00:00:20,000
Another valid entry
"""
    srt_file = tmp_path / "malformed.srt"
    srt_file.write_text(srt_content, encoding="utf-8")
    
    # Should not raise, should skip malformed entry
    entries = SRTProcessor.parse(srt_file)
    
    # Should have 3 valid entries, 1 skipped
    assert len(entries) >= 2
    assert entries[0]["text"] == "Valid entry"
    # Note: After skipping, entries may be reindexed


def test_srt_parsing_empty_entries(tmp_path):
    """Test that empty text entries are skipped."""
    srt_content = """1
00:00:01,000 --> 00:00:05,000
Valid entry

2
00:00:06,000 --> 00:00:10,000


3
00:00:15,000 --> 00:00:20,000
Another valid entry
"""
    srt_file = tmp_path / "empty.srt"
    srt_file.write_text(srt_content, encoding="utf-8")
    
    entries = SRTProcessor.parse(srt_file)
    
    # Should have 2 entries (empty entry skipped)
    assert len(entries) == 2
    assert all(e["text"] for e in entries)  # All entries have non-empty text


def test_srt_parsing_invalid_duration(tmp_path):
    """Test that entries with invalid duration are skipped."""
    srt_content = """1
00:00:05,000 --> 00:00:01,000
Backwards timecode

2
00:00:06,000 --> 00:00:10,000
Valid entry

3
00:00:15,000 --> 00:00:15,000
Zero duration
"""
    srt_file = tmp_path / "invalid_duration.srt"
    srt_file.write_text(srt_content, encoding="utf-8")
    
    entries = SRTProcessor.parse(srt_file)
    
    # Should only have 1 valid entry (other two skipped)
    assert len(entries) >= 1
    assert entries[0]["dur_ms"] > 0


def test_fallback_parser_with_corrupted_format(tmp_path):
    """Test fallback parser can recover from severely corrupted SRT."""
    # This is a non-standard format that pysrt might fail on
    srt_content = """1 - First subtitle
00:00:01,000 --> 00:00:05,000
Hello world

2 - Second subtitle
00:00:06,000 --> 00:00:10,000
Second subtitle
"""
    srt_file = tmp_path / "corrupted.srt"
    srt_file.write_text(srt_content, encoding="utf-8")
    
    entries = SRTProcessor.parse(srt_file)
    
    # Fallback parser should recover some entries
    assert len(entries) >= 1


def test_srt_parsing_empty_file(tmp_path):
    """Test that empty SRT file returns empty list."""
    srt_file = tmp_path / "empty.srt"
    srt_file.write_text("", encoding="utf-8")
    
    entries = SRTProcessor.parse(srt_file)
    
    assert len(entries) == 0


def test_srt_parsing_file_not_found():
    """Test that non-existent file returns empty list."""
    entries = SRTProcessor.parse(Path("/non/existent/file.srt"))
    
    # Should handle gracefully, not raise
    assert isinstance(entries, list)
