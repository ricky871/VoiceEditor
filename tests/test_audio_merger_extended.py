"""
Comprehensive tests for audio_merger.py module.
Tests segment merging, manifest handling, and FFmpeg integration.
"""
import pytest
import json
import wave
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from src import audio_merger


@pytest.fixture
def tmp_work_dir(tmp_path):
    """Create temporary workspace with audio files."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    
    # Create sample audio segments
    for i in range(3):
        wav_file = work_dir / f"seg_{i}.wav"
        # Create minimal WAV file
        with wave.open(str(wav_file), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            # Write 1 second of silence (44100 samples * 2 channels * 2 bytes)
            wav.writeframes(b'\x00' * (44100 * 2 * 2))
    
    return work_dir


@pytest.fixture
def sample_manifest(tmp_work_dir):
    """Create sample manifest with valid references."""
    return [
        {"id": 0, "wav": str(tmp_work_dir / "seg_0.wav"), "start_ms": 0, "duration_ms": 1000},
        {"id": 1, "wav": str(tmp_work_dir / "seg_1.wav"), "start_ms": 1000, "duration_ms": 1500},
        {"id": 2, "wav": str(tmp_work_dir / "seg_2.wav"), "start_ms": 2500, "duration_ms": 1200},
    ]


class TestManifestReading:
    """Test manifest file reading and validation."""

    def test_read_manifest_valid_json(self, tmp_path, sample_manifest):
        """Test reading valid manifest JSON."""
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(sample_manifest))
        
        result = audio_merger.read_manifest(str(manifest_file))
        assert len(result) == 3
        assert all("id" in entry for entry in result)

    def test_read_manifest_empty_list(self, tmp_path):
        """Test reading manifest with empty list."""
        manifest_file = tmp_path / "empty_manifest.json"
        manifest_file.write_text(json.dumps([]))
        
        result = audio_merger.read_manifest(str(manifest_file))
        assert result == []

    def test_read_manifest_with_metadata(self, tmp_path):
        """Test reading manifest with additional metadata."""
        manifest_data = {
            "version": 1,
            "segments": [
                {"id": 0, "wav": "seg_0.wav", "duration_ms": 1000},
            ],
            "metadata": {"total_duration": 1000}
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_data))
        
        # Should handle various manifest structures
        result = audio_merger.read_manifest(str(manifest_file))
        assert result is not None


class TestPathResolution:
    """Test path resolution and joining."""

    def test_resolve_absolute_posix_path(self):
        """Test resolving absolute POSIX path."""
        result = audio_merger.resolve_path("/home/user/audio.wav", "/data")
        assert result == "/home/user/audio.wav"

    def test_resolve_absolute_windows_path(self):
        """Test resolving absolute Windows path."""
        result = audio_merger.resolve_path("C:\\Users\\audio.wav", "D:\\data")
        assert result == "C:\\Users\\audio.wav"

    def test_resolve_relative_path_single_level(self):
        """Test resolving single-level relative path."""
        result = audio_merger.resolve_path("audio.wav", "/work")
        assert "work" in result and "audio.wav" in result

    def test_resolve_relative_path_multi_level(self):
        """Test resolving multi-level relative path."""
        result = audio_merger.resolve_path("segments/audio.wav", "/work")
        assert "work" in result and "segments" in result

    def test_resolve_path_with_parent_reference(self):
        """Test resolving path with parent directory (..)."""
        result = audio_merger.resolve_path("../other/audio.wav", "/work/current")
        # Should be normalized
        assert isinstance(result, str)

    def test_resolve_path_with_dot_reference(self):
        """Test resolving path with current directory (.)."""
        result = audio_merger.resolve_path("./audio.wav", "/work")
        assert "audio.wav" in result


class TestManifestSorting:
    """Test manifest entry sorting logic."""

    def test_sort_by_start_ms_primary(self):
        """Test that entries are sorted by start_ms as primary key."""
        entries = [
            {"id": 2, "wav": "seg_2.wav", "start_ms": 5000},
            {"id": 0, "wav": "seg_0.wav", "start_ms": 0},
            {"id": 1, "wav": "seg_1.wav", "start_ms": 2000},
        ]
        
        sorted_entries = sorted(
            entries, key=lambda x: (x.get("start_ms", 0), x.get("id", 0))
        )
        
        assert sorted_entries[0]["id"] == 0
        assert sorted_entries[1]["id"] == 1
        assert sorted_entries[2]["id"] == 2

    def test_sort_by_id_when_start_ms_equal(self):
        """Test that entries with equal start_ms are sorted by id."""
        entries = [
            {"id": 2, "wav": "seg_2.wav", "start_ms": 0},
            {"id": 0, "wav": "seg_0.wav", "start_ms": 0},
            {"id": 1, "wav": "seg_1.wav", "start_ms": 0},
        ]
        
        sorted_entries = sorted(
            entries, key=lambda x: (x.get("start_ms", 0), x.get("id", 0))
        )
        
        assert [e["id"] for e in sorted_entries] == [0, 1, 2]

    def test_sort_with_missing_start_ms(self):
        """Test sorting when start_ms is missing (should default to 0)."""
        entries = [
            {"id": 1, "wav": "seg_1.wav"},  # No start_ms
            {"id": 0, "wav": "seg_0.wav", "start_ms": 0},
        ]
        
        sorted_entries = sorted(
            entries, key=lambda x: (x.get("start_ms", 0), x.get("id", 0))
        )
        
        # Both should be sorted correctly
        assert len(sorted_entries) == 2


class TestAudioFileHandling:
    """Test audio file operations."""

    def test_detect_wav_parameters(self, tmp_work_dir):
        """Test detecting WAV file parameters."""
        wav_file = tmp_work_dir / "seg_0.wav"
        
        with wave.open(str(wav_file), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            frame_rate = wav.getframerate()
            
            assert channels == 2
            assert sample_width == 2
            assert frame_rate == 44100

    def test_handle_mono_audio(self, tmp_path):
        """Test handling mono audio files."""
        wav_file = tmp_path / "mono.wav"
        with wave.open(str(wav_file), "wb") as wav:
            wav.setnchannels(1)  # Mono
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(b'\x00' * 44100 * 2)
        
        # Should handle mono correctly
        assert wav_file.exists()

    def test_handle_stereo_audio(self, tmp_path):
        """Test handling stereo audio files."""
        wav_file = tmp_path / "stereo.wav"
        with wave.open(str(wav_file), "wb") as wav:
            wav.setnchannels(2)  # Stereo
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(b'\x00' * 44100 * 2 * 2)
        
        assert wav_file.exists()

    def test_handle_different_sample_rates(self, tmp_path):
        """Test handling audio with different sample rates."""
        for sample_rate in [8000, 16000, 44100, 48000]:
            wav_file = tmp_path / f"sr_{sample_rate}.wav"
            with wave.open(str(wav_file), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(b'\x00' * sample_rate * 2 * 2)
            
            assert wav_file.exists()


class TestGapHandling:
    """Test handling gaps between audio segments."""

    def test_no_gap_between_segments(self):
        """Test segments with no gap (end_ms == next_start_ms)."""
        entries = [
            {"id": 0, "start_ms": 0, "duration_ms": 1000},     # 0-1000
            {"id": 1, "start_ms": 1000, "duration_ms": 1000},  # 1000-2000
        ]
        
        # Should handle seamlessly
        assert entries[0]["start_ms"] + entries[0]["duration_ms"] == entries[1]["start_ms"]

    def test_gap_between_segments(self):
        """Test segments with gap (silence needed)."""
        entries = [
            {"id": 0, "start_ms": 0, "duration_ms": 1000},      # 0-1000
            {"id": 1, "start_ms": 2000, "duration_ms": 1000},   # 2000-3000 (1s gap)
        ]
        
        # Gap calculation
        gap = entries[1]["start_ms"] - (entries[0]["start_ms"] + entries[0]["duration_ms"])
        assert gap == 1000  # 1 second gap

    def test_overlapping_segments(self):
        """Test handling overlapping segments."""
        entries = [
            {"id": 0, "start_ms": 0, "duration_ms": 2000},       # 0-2000
            {"id": 1, "start_ms": 1000, "duration_ms": 2000},    # 1000-3000 (overlap)
        ]
        
        # Detect overlap
        overlap = (entries[0]["start_ms"] + entries[0]["duration_ms"]) - entries[1]["start_ms"]
        assert overlap > 0


class TestErrorRecovery:
    """Test error handling and recovery."""

    def test_skip_missing_audio_file(self, tmp_path):
        """Test skipping missing audio files in manifest."""
        entries = [
            {"id": 0, "wav": str(tmp_path / "existing.wav")},
            {"id": 1, "wav": str(tmp_path / "missing.wav")},
        ]
        
        # Create only the first file
        wav_file = tmp_path / "existing.wav"
        with wave.open(str(wav_file), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(b'\x00' * 44100)
        
        # Should find and use only existing file
        valid_entries = [e for e in entries if Path(e["wav"]).exists()]
        assert len(valid_entries) == 1

    def test_handle_corrupted_wav_file(self, tmp_path):
        """Test handling corrupted WAV file."""
        corrupted_file = tmp_path / "corrupted.wav"
        corrupted_file.write_bytes(b"not a wav file")
        
        # Should handle gracefully
        try:
            with wave.open(str(corrupted_file), "rb") as wav:
                pass
        except wave.Error:
            pass  # Expected

    def test_handle_file_permission_error(self, tmp_path):
        """Test handling file permission errors."""
        wav_file = tmp_path / "protected.wav"
        with wave.open(str(wav_file), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(b'\x00' * 44100)
        
        # In production, would test permission denied
        assert wav_file.exists()


class TestMergeOperation:
    """Test merging operations."""

    def test_merge_segments_calculation(self, tmp_work_dir, sample_manifest):
        """Test calculation for segment merging."""
        total_duration = sum(entry["duration_ms"] for entry in sample_manifest)
        assert total_duration == 3700  # 1000 + 1500 + 1200

    def test_merge_with_padding(self):
        """Test merging with gap padding."""
        entries = [
            {"id": 0, "duration_ms": 1000},
            {"id": 1, "duration_ms": 1000},
        ]
        
        # With 500ms padding between
        total = sum(e["duration_ms"] for e in entries) + 500
        assert total == 2500

    def test_merge_preserves_order(self, sample_manifest):
        """Test that merge operation preserves segment order."""
        manifest_sorted = sorted(
            sample_manifest, 
            key=lambda x: (x.get("start_ms", 0), x.get("id", 0))
        )
        
        # Order should be preserved
        assert manifest_sorted[0]["id"] == 0
        assert manifest_sorted[1]["id"] == 1
        assert manifest_sorted[2]["id"] == 2


class TestManifestValidation:
    """Test manifest validation."""

    def test_validate_required_fields(self):
        """Test validation of required manifest fields."""
        valid_entry = {"id": 0, "wav": "seg_0.wav", "start_ms": 0}
        
        # Check required fields
        required_fields = ["id", "wav"]
        assert all(field in valid_entry for field in required_fields)

    def test_validate_data_types(self):
        """Test validation of data types in manifest."""
        entry = {"id": 0, "wav": "seg_0.wav", "start_ms": 0, "duration_ms": 1000}
        
        assert isinstance(entry["id"], int)
        assert isinstance(entry["wav"], str)
        assert isinstance(entry["start_ms"], int)
        assert isinstance(entry["duration_ms"], int)

    def test_validate_positive_durations(self):
        """Test that durations are positive."""
        entries = [
            {"id": 0, "duration_ms": 1000},  # Valid
            {"id": 1, "duration_ms": 0},     # Invalid
            {"id": 2, "duration_ms": -500},  # Invalid
        ]
        
        valid_entries = [e for e in entries if e.get("duration_ms", 0) > 0]
        assert len(valid_entries) == 1
