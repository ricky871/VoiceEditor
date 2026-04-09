"""
Comprehensive tests for audio_merger.py coverage.
Focuses on merge_segments, ffmpeg integration, and CLI runner.
"""
import pytest
import json
import wave
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from src import audio_merger

@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "out").mkdir()
    return ws

@pytest.fixture
def sample_wav(workspace):
    """Create a sample WAV file."""
    wav_path = workspace / "test.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        # 0.1 second of silence
        wf.writeframes(b'\x00' * int(44100 * 2 * 0.1))
    return wav_path

class TestMergeSegments:
    """Test merge_segments function."""

    def test_merge_segments_basic(self, workspace, sample_wav):
        """Test basic merging of two segments."""
        manifest = [
            {"id": 0, "wav": "test.wav", "start_ms": 0},
            {"id": 1, "wav": "test.wav", "start_ms": 100}
        ]
        out_path = workspace / "out" / "merged.wav"
        
        audio_merger.merge_segments(manifest, str(out_path), workspace_root=str(workspace))
        
        assert out_path.exists()
        with wave.open(str(out_path), "rb") as wf:
            # 0.1s + 0.1s = 0.2s
            # 44100 * 0.2 = 8820 frames
            assert wf.getnframes() == 8820

    def test_merge_segments_with_padding(self, workspace, sample_wav):
        """Test merging with padding gaps."""
        manifest = [
            {"id": 0, "wav": "test.wav", "start_ms": 0},
            {"id": 1, "wav": "test.wav", "start_ms": 500} # 400ms gap
        ]
        out_path = workspace / "out" / "padded.wav"
        
        audio_merger.merge_segments(manifest, str(out_path), pad_gaps=True, workspace_root=str(workspace))
        
        assert out_path.exists()
        with wave.open(str(out_path), "rb") as wf:
            # 0.1s segment + 0.4s silence + 0.1s segment = 0.6s
            # 44100 * 0.6 = 26460 frames
            assert wf.getnframes() >= 26400  # Allow slight rounding

    def test_merge_segments_incompatible_params(self, workspace, sample_wav):
        """Test error when merging incompatible WAV files."""
        # Create a stereo WAV
        stereo_wav = workspace / "stereo.wav"
        with wave.open(str(stereo_wav), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(b'\x00' * 100)
            
        manifest = [
            {"id": 0, "wav": "test.wav"},
            {"id": 1, "wav": "stereo.wav"}
        ]
        out_path = workspace / "out" / "error.wav"
        
        with pytest.raises(RuntimeError, match="Incompatible WAV params"):
            audio_merger.merge_segments(manifest, str(out_path), workspace_root=str(workspace))

    def test_merge_segments_empty_manifest(self, workspace):
        """Test error when manifest has no valid segments."""
        with pytest.raises(RuntimeError, match="No valid segments found to merge"):
            audio_merger.merge_segments([], "out.wav", workspace_root=str(workspace))

    def test_merge_segments_missing_file_warning(self, workspace, capsys):
        """Test warning when a segment file is missing."""
        manifest = [{"id": 0, "wav": "missing.wav"}]
        with pytest.raises(RuntimeError, match="No valid segments found to merge"):
            audio_merger.merge_segments(manifest, "out.wav", workspace_root=str(workspace))
        
        captured = capsys.readouterr()
        assert "Warning: Missing file" in captured.out

class TestFFmpegIntegration:
    """Test FFmpeg integration functions."""

    def test_ensure_safe_srt_for_ffmpeg_no_special_chars(self, tmp_path):
        """Test path processing for normal paths."""
        # Use a path explicitly without problematic characters.
        # Ensure it doesn't trigger the "has_problematic" check.
        srt_path = Path(tmp_path) / "normal.srt"
        srt_path.touch()
        
        # Mock problematic_chars check to ensure it returns False regardless of tmp_path content
        with patch("src.audio_merger.Path.resolve", return_value=srt_path):
            result = audio_merger.ensure_safe_srt_for_ffmpeg(str(srt_path))
            if os.name == 'nt':
                # On Windows, it should at least contain the escaped colon if it stayed in place
                # OR it might have been copied if tmp_path itself has spaces.
                # If it stayed in place, it will have \:
                if "\\:" in result:
                    assert True
                else:
                    # If it was copied, it's also acceptable as long as it exists
                    assert "ffmpeg_safe_subtitles.srt" in result
            else:
                assert "normal.srt" in result or "ffmpeg_safe_subtitles.srt" in result

    def test_ensure_safe_srt_for_ffmpeg_with_spaces(self, tmp_path):
        """Test path processing for paths with spaces."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        srt_path = tmp_path / "path with spaces.srt"
        srt_path.touch()

        result = audio_merger.ensure_safe_srt_for_ffmpeg(str(srt_path), work_dir=str(work_dir))

        # Should copy to safe location on disk
        safe_copy = work_dir / "ffmpeg_safe_subtitles.srt"
        assert safe_copy.exists()
        # Returned value is FFmpeg filter-ready: forward slashes, colon escaped
        assert "ffmpeg_safe_subtitles.srt" in result
        assert "\\" not in result or result.count("\\") == result.count("\\:")  # only \: escapes, no raw backslashes

    @patch("src.audio_merger.ffmpeg_available", return_value=True)
    @patch("subprocess.run")
    def test_merge_video_with_audio_and_subs_basic(self, mock_run, mock_ffmpeg, tmp_path):
        """Test basic video merging command generation."""
        mock_run.return_value = MagicMock(returncode=0)
        
        video_in = tmp_path / "in.mp4"
        audio_in = tmp_path / "in.wav"
        video_in.touch()
        audio_in.touch()
        output = tmp_path / "out.mp4"
        
        audio_merger.merge_video_with_audio_and_subs(
            str(video_in), str(audio_in), None, str(output)
        )
        
        args = mock_run.call_args[0][0]
        assert "ffmpeg" in args
        assert str(video_in) in args
        assert str(audio_in) in args
        assert str(output) in args

    @patch("src.audio_merger.ffmpeg_available", return_value=True)
    @patch("subprocess.run")
    def test_merge_video_with_audio_and_subs_burn(self, mock_run, mock_ffmpeg, tmp_path):
        """Test video merging with burned subtitles."""
        mock_run.return_value = MagicMock(returncode=0)
        
        video_in = tmp_path / "in.mp4"
        audio_in = tmp_path / "in.wav"
        subs_in = tmp_path / "subs.srt"
        video_in.touch()
        audio_in.touch()
        subs_in.touch()
        output = tmp_path / "out.mp4"
        
        audio_merger.merge_video_with_audio_and_subs(
            str(video_in), str(audio_in), str(subs_in), str(output), burn_subs=True
        )
        
        args = mock_run.call_args[0][0]
        assert "-filter_complex" in args
        # Check if subtitles filter is present
        assert any("subtitles=" in arg for arg in args)

    @patch("src.audio_merger.ffmpeg_available", return_value=True)
    @patch("subprocess.run")
    def test_merge_video_with_audio_and_subs_ffmpeg_failure(self, mock_run, mock_ffmpeg, tmp_path):
        """Test handling of ffmpeg failure."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Error message")
        
        video_in = tmp_path / "in.mp4"
        audio_in = tmp_path / "in.wav"
        video_in.touch()
        audio_in.touch()
        output = tmp_path / "out.mp4"
        
        with pytest.raises(RuntimeError, match="ffmpeg failed: Error message"):
            audio_merger.merge_video_with_audio_and_subs(
                str(video_in), str(audio_in), None, str(output)
            )

class TestVideoCandidate:
    """Test find_video_candidate function."""

    def test_find_video_candidate_by_subs(self, tmp_path):
        """Test finding video matching subtitle filename."""
        video = tmp_path / "movie.mp4"
        video.touch()
        subs = tmp_path / "movie.srt"
        
        result = audio_merger.find_video_candidate(str(subs))
        assert result == str(video)

    def test_find_video_candidate_in_workspace(self, tmp_path):
        """Test finding any video in workspace."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        video = workspace / "clip.mkv"
        video.touch()
        
        result = audio_merger.find_video_candidate(workspace_root=str(workspace))
        assert result == str(video)

class TestCLIRunner:
    """Test the run_audio_merger function."""

    @patch("src.audio_merger.read_manifest")
    @patch("src.audio_merger.merge_segments")
    @patch("src.audio_merger.merge_video_with_audio_and_subs")
    def test_run_audio_merger_full(self, mock_video, mock_merge, mock_read):
        """Test full run with video processing."""
        mock_read.return_value = [{"id": 0}]
        
        args = MagicMock()
        args.manifest = "m.json"
        args.out = "out.wav"
        args.pad_gaps = True
        args.workspace = "ws"
        args.video = "vid.mp4"
        args.subs = "s.srt"
        args.output_video = "final.mp4"
        args.burn_subs = True
        
        # Mock existence for video and audio
        with patch("os.path.exists", return_value=True):
            audio_merger.run_audio_merger(args)
        
        mock_read.assert_called_with("m.json")
        mock_merge.assert_called_once()
        mock_video.assert_called_once_with(
            "vid.mp4", "out.wav", "s.srt", "final.mp4", burn_subs=True
        )

    @patch("src.audio_merger.ffmpeg_available", return_value=False)
    def test_ffmpeg_not_available(self, mock_ffmpeg, tmp_path):
        """Test error when ffmpeg is missing."""
        with pytest.raises(RuntimeError, match="ffmpeg not found in PATH"):
            audio_merger.merge_video_with_audio_and_subs("v.mp4", "a.wav", None, "o.mp4")
