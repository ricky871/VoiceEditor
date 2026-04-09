"""
Tests for audio_merger.py module functions.
"""
import pytest
import json
from pathlib import Path
from src import audio_merger


class TestManifestHandling:
    """Test manifest reading and handling."""

    def test_read_manifest_success(self, tmp_path):
        """Test reading valid manifest file."""
        manifest_file = tmp_path / "manifest.json"
        manifest_data = [
            {"id": 0, "wav": "seg_0.wav", "start_ms": 0, "duration_ms": 1500},
            {"id": 1, "wav": "seg_1.wav", "start_ms": 1500, "duration_ms": 2000},
        ]
        manifest_file.write_text(json.dumps(manifest_data))
        
        manifest = audio_merger.read_manifest(str(manifest_file))
        assert len(manifest) == 2
        assert manifest[0]["id"] == 0

    def test_read_manifest_invalid_json(self, tmp_path):
        """Test reading manifest with invalid JSON."""
        invalid_file = tmp_path / "invalid.json"
        invalid_file.write_text("{invalid json}")
        
        with pytest.raises(json.JSONDecodeError):
            audio_merger.read_manifest(str(invalid_file))

    def test_read_manifest_missing_file(self, tmp_path):
        """Test reading non-existent manifest file."""
        missing_file = tmp_path / "nonexistent.json"
        
        with pytest.raises(FileNotFoundError):
            audio_merger.read_manifest(str(missing_file))


class TestPathResolution:
    """Test path resolution utilities."""

    def test_resolve_absolute_path(self):
        """Test resolving absolute paths are unchanged."""
        result = audio_merger.resolve_path("/absolute/path", "/base")
        assert result == "/absolute/path"

    def test_resolve_relative_path_joined(self):
        """Test resolving relative paths are joined with base."""
        result = audio_merger.resolve_path("relative/path", "/base")
        assert "base" in result and "relative" in result


class TestManifestSorting:
    """Test manifest sorting operations."""

    def test_entries_sorted_by_start_ms(self):
        """Test entries are sorted by start_ms then id."""
        entries = [
            {"id": 2, "wav": "seg_2.wav", "start_ms": 3000},
            {"id": 0, "wav": "seg_0.wav", "start_ms": 0},
            {"id": 1, "wav": "seg_1.wav", "start_ms": 1500},
        ]
        sorted_entries = sorted(
            entries, key=lambda x: (x.get("start_ms", 0), x.get("id", 0))
        )
        
        assert sorted_entries[0]["id"] == 0
        assert sorted_entries[1]["id"] == 1
        assert sorted_entries[2]["id"] == 2

    def test_entries_with_missing_start_ms(self):
        """Test sorting when start_ms is missing (defaults to 0)."""
        entries = [
            {"id": 1, "wav": "seg_1.wav"},  # No start_ms
            {"id": 0, "wav": "seg_0.wav", "start_ms": 0},
        ]
        sorted_entries = sorted(
            entries, key=lambda x: (x.get("start_ms", 0), x.get("id", 0))
        )
        
        assert sorted_entries[0]["id"] == 0
        assert sorted_entries[1]["id"] == 1
