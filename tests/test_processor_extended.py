"""
Extended coverage for src.tts.processor module.
Testing edge cases in SRT parsing, diagnostics, and TTSSynthesizer.
"""
import pytest
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.tts.processor import SRTProcessor, TTSSynthesizer, _get_gpu_diagnostics, _format_segment_diagnostic

class TestSRTProcessorExtended:
    def test_parse_with_malformed_entries(self, tmp_path):
        """Test parsing SRT with malformed entries that should be skipped."""
        srt_content = """1
00:00:01,000 --> 00:00:03,000
Valid entry

2
00:00:04,000 --> 00:00:02,000
Invalid duration (end < start)

3
00:00:05,000 --> 00:00:05,000
Zero duration

4

Empty text entry
"""
        srt_file = tmp_path / "malformed.srt"
        srt_file.write_text(srt_content, encoding="utf-8")
        
        entries = SRTProcessor.parse(srt_file)
        # Only entry 1 is valid. 
        # Entry 2 has invalid duration. 
        # Entry 3 has zero duration.
        # Entry 4 depends on pysrt behavior for empty index/time, but likely fails.
        assert len(entries) == 1
        assert entries[0]["text"] == "Valid entry"

    def test_parse_fallback(self, tmp_path):
        """Test manual fallback parser for non-standard SRT files."""
        # Note: Code uses regex: (\d{2}):(\d{2}):(\d{2})[,:](\d{3})
        # So only comma or colon are allowed separators before milliseconds.
        srt_content = """1
00:00:01,000 --> 00:00:03,000
Line 1
Line 2

2
00:00:04:500 --> 00:00:06:123
Colon separator in timecode
"""
        srt_file = tmp_path / "fallback.srt"
        srt_file.write_text(srt_content, encoding="utf-8")
        
        # Force fallback by mocking pysrt.open to fail
        with patch("src.tts.processor.pysrt.open", side_effect=Exception("Mock failure")):
            entries = SRTProcessor.parse(srt_file)
            assert len(entries) == 2
            assert entries[0]["text"] == "Line 1 Line 2"
            assert entries[0]["start_ms"] == 1000
            assert entries[1]["start_ms"] == 4500
            assert entries[1]["end_ms"] == 6123

    def test_guess_video(self, tmp_path):
        """Test video guessing logic."""
        srt_path = tmp_path / "movie.srt"
        srt_path.touch()
        
        # 1. Exact match .mp4
        mp4_path = tmp_path / "movie.mp4"
        mp4_path.touch()
        assert SRTProcessor.guess_video(srt_path) == mp4_path
        mp4_path.unlink()
        
        # 2. Other extensions
        mkv_path = tmp_path / "movie.mkv"
        mkv_path.touch()
        assert SRTProcessor.guess_video(srt_path) == mkv_path
        mkv_path.unlink()
        
        # 3. No match
        assert SRTProcessor.guess_video(srt_path) is None

class TestDiagnostics:
    def test_get_gpu_diagnostics_no_torch(self):
        """Test GPU diagnostics when torch is not available or fails."""
        with patch.dict("sys.modules", {"torch": None}):
            diag = _get_gpu_diagnostics()
            assert diag["gpu_available"] is False

    def test_get_gpu_diagnostics_with_mock_torch(self):
        """Test GPU diagnostics with mocked torch."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.version.cuda = "11.7"
        mock_torch.cuda.device_count.return_value = 1
        mock_torch.cuda.memory_allocated.return_value = 1e9
        mock_torch.cuda.memory_reserved.return_value = 2e9
        
        with patch.dict("sys.modules", {"torch": mock_torch}):
            diag = _get_gpu_diagnostics()
            assert diag["gpu_available"] is True
            assert diag["cuda_version"] == "11.7"
            assert diag["gpu_memory_allocated_gb"] == 1.0

    def test_format_segment_diagnostic(self):
        """Test diagnostic formatting with various error types."""
        entry = {"id": 1, "text": "Hello", "dur_ms": 1000}
        
        # OOM Error
        exc_oom = Exception("CUDA out of memory. Tried to allocate...")
        diag_oom = _format_segment_diagnostic(entry, exc_oom, "InferenceError")
        assert "Suggestion: Reduce diffusion_steps" in diag_oom
        
        # Permission Error
        exc_perm = Exception("Permission denied: 'work/out.wav'")
        diag_perm = _format_segment_diagnostic(entry, exc_perm, "FileError")
        assert "Suggestion: Check write permissions" in diag_perm

class TestTTSSynthesizerExtended:
    def test_build_duration_candidates(self):
        config = MagicMock()
        config.tokens_per_sec = 100
        syn = TTSSynthesizer(None, config)
        
        candidates = syn.build_duration_candidates(1000) # 1 second
        assert candidates == [{"max_mel_tokens": 100}, {"max_generate_length": 100}]

    @patch("src.tts.processor._clear_gpu_cache")
    def test_perform_batch_cleanup(self, mock_clear, caplog):
        syn = TTSSynthesizer(None, MagicMock())
        syn._perform_batch_cleanup(1)
        mock_clear.assert_called_once()

    def test_safe_infer_retries(self):
        """Test that safe_infer retries on failure."""
        mock_tts = MagicMock()
        # Fail twice, then succeed
        mock_tts.infer.side_effect = [Exception("Fail 1"), Exception("Fail 2"), None]
        
        config = MagicMock()
        config.max_retries = 3
        syn = TTSSynthesizer(mock_tts, config)
        
        candidates = [{"param": 1}]
        syn.safe_infer({"text": "test"}, candidates, 1)
        
        assert mock_tts.infer.call_count == 3

    def test_safe_infer_type_error_fallback(self):
        """Test fallback to next candidate on TypeError with param name."""
        mock_tts = MagicMock()
        # First candidate fails with TypeError about 'old_param'
        mock_tts.infer.side_effect = [TypeError("unexpected keyword argument 'old_param'"), None]
        
        config = MagicMock()
        config.max_retries = 1
        syn = TTSSynthesizer(mock_tts, config)
        
        candidates = [{"old_param": 1}, {"new_param": 2}]
        syn.safe_infer({"text": "test"}, candidates, 1)
        
        assert mock_tts.infer.call_count == 2
        # Verify first call used old_param, second used new_param
        assert "old_param" in mock_tts.infer.call_args_list[0][1]
        assert "new_param" in mock_tts.infer.call_args_list[1][1]
