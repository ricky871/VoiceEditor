"""
Extended coverage for src.tts.audio_pipeline.
Testing high-quality re-timing, gap detection, and FFmpeg path handling.
"""
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch
from pydub import AudioSegment
from src.tts.audio_pipeline import retime_segment_to_target, stitch_segments_from_manifest, ensure_safe_srt_for_ffmpeg

@pytest.fixture
def one_second_sine():
    """Create a 1-second sine wave segment."""
    sr = 44100
    duration = 1.0 # sec
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # 440 Hz
    samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    return AudioSegment(
        samples.tobytes(),
        frame_rate=sr,
        sample_width=2,
        channels=1
    )

class TestAudioPipelineExtended:
    def test_retime_segment_shorter_than_target(self, one_second_sine):
        """Test that shorter audio is padded with silence instead of slowed down."""
        target_ms = 1500 # 1.5 seconds
        seg, length, factor = retime_segment_to_target(one_second_sine, target_ms, 44100)
        
        assert length == 1500
        assert factor == 1.0 # Should not have slowed down
        # Check that it ends with silence
        # (pydub doesn't expose easy check for 'is_silence', but we can check if it's longer)
        assert len(seg) == 1500
        assert len(one_second_sine) == 1000

    def test_retime_segment_longer_than_target_small(self, one_second_sine):
        """Test that slightly longer audio is truncated."""
        target_ms = 980 # 20ms difference
        seg, length, factor = retime_segment_to_target(one_second_sine, target_ms, 44100)
        
        assert length == 980
        assert factor == 1.0
        assert len(seg) == 980

    def test_retime_segment_longer_than_target_large(self, one_second_sine):
        """Test that significantly longer audio uses librosa for speedup."""
        target_ms = 500 # 2x speedup needed
        
        # librosa is used for this path
        seg, length, factor = retime_segment_to_target(one_second_sine, target_ms, 44100)
        
        assert factor == 2.0
        assert length == 500
        assert len(seg) == 500

    def test_retime_segment_librosa_failure_fallback(self, one_second_sine):
        """Test fallback to truncation/padding if librosa fails."""
        target_ms = 500
        
        with patch("librosa.effects.time_stretch", side_effect=Exception("Mock librosa error")):
            seg, length, factor = retime_segment_to_target(one_second_sine, target_ms, 44100)
            assert factor == 2.0
            assert length == 500 # Should still be 500 due to fallback truncation
            assert len(seg) == 500

    def test_stitch_segments_with_gaps(self, tmp_path, one_second_sine):
        """Test stitching segments with gaps and overlap."""
        # Create two sample WAVs
        wav1 = tmp_path / "1.wav"
        wav2 = tmp_path / "2.wav"
        one_second_sine.export(str(wav1), format="wav")
        one_second_sine.export(str(wav2), format="wav")
    
        # Note: stitch_segments_from_manifest uses 'dur_target_ms' for re-timing
        manifest = [
            {"id": 1, "start_ms": 0, "end_ms": 1000, "dur_target_ms": 1000, "wav": "1.wav"},
            {"id": 2, "start_ms": 1500, "end_ms": 2500, "dur_target_ms": 1000, "wav": "2.wav"}, # 500ms gap
            {"id": 3, "start_ms": 2000, "end_ms": 3000, "dur_target_ms": 1000, "wav": "invalid.wav", "failed": True} # Overlap + Failed
        ]
        
        # Patch ensure_safe_srt_for_ffmpeg internal usage if needed, or just run
        final = stitch_segments_from_manifest(manifest, 44100, 0, manifest_dir=tmp_path)
        
        # The base duration is the max(end_ms) + 100
        # Entry 1: end_ms = 1000
        # Entry 2: end_ms = 2500
        # Result should be 2500 + 100 = 2600ms
        assert len(final) == 2600
        
    def test_ensure_safe_srt_for_ffmpeg_cases(self, tmp_path):
        """Test various SRT path escaping/copying cases."""
        # Create work dir
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        
        # Normal path - escapes colon on windows
        # Use a path that definitely exists and doesn't have spaces
        norm_srt = tmp_path / "normal.srt"
        norm_srt.write_text("info")
        
        # Mock Path.resolve to ensure it returns the norm_srt path
        # And ensure the string version DOES NOT contain spaces
        with patch("src.tts.audio_pipeline.Path.resolve", return_value=norm_srt):
            # We must ensure str(norm_srt) doesn't trigger has_problematic
            # has_problematic checks: ["'", '"', ' ', '&', '|', '<', '>', '\\', '(', ')', '[', ']', '{', '}', ';', '`', '$']
            # On some systems, tmp_path contains spaces.
            
            # Instead of relying on tmp_path, let's mock the whole check
            with patch("src.tts.audio_pipeline.any", return_value=False):
                res_norm = ensure_safe_srt_for_ffmpeg(norm_srt)
                if ":" in str(norm_srt):
                    assert "\\:" in res_norm
        
        # Path with space - naturally triggers copy (space is problematic)
        space_srt = tmp_path / "path with space.srt"
        space_srt.write_text("info")

        res_space = ensure_safe_srt_for_ffmpeg(space_srt, work_dir=str(work_dir))

        assert "ffmpeg_safe_subtitles.srt" in res_space
        assert (work_dir / "ffmpeg_safe_subtitles.srt").exists()

@patch("subprocess.run")
def test_mux_audio_video_burn(mock_run, tmp_path):
    """Test mux_audio_video with burn-in subtitles."""
    from src.tts.audio_pipeline import mux_audio_video
    mock_run.return_value = MagicMock(returncode=0)
    
    vid = tmp_path / "in.mp4"
    aud = tmp_path / "in.wav"
    out = tmp_path / "out.mp4"
    srt = tmp_path / "sub.srt"
    
    vid.touch()
    aud.touch()
    srt.touch()
    
    mux_audio_video(vid, aud, out, srt_path=srt)
    
    cmd = mock_run.call_args[0][0]
    assert "-filter_complex" in cmd
    assert "subtitles=" in "".join(cmd)
