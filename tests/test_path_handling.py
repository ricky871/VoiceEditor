from pathlib import Path

from pydub import AudioSegment

from src.tts.audio_pipeline import ensure_safe_srt_for_ffmpeg, stitch_segments_from_manifest


def test_ensure_safe_srt_for_ffmpeg_copies_problematic_path(tmp_path):
    source_dir = tmp_path / "sub dir"
    source_dir.mkdir()
    srt_file = source_dir / "quote's subtitles.srt"
    srt_file.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

    result = ensure_safe_srt_for_ffmpeg(srt_file, work_dir=str(tmp_path / "work"))

    # The function returns an FFmpeg filter-ready string (forward slashes, colon escaped).
    # The actual safe copy should exist on disk.
    safe_copy = tmp_path / "work" / "ffmpeg_safe_subtitles.srt"
    assert safe_copy.exists()
    assert safe_copy.read_text(encoding="utf-8") == srt_file.read_text(encoding="utf-8")
    # Returned string uses forward slashes and has Windows drive colon escaped (if on Windows)
    assert "ffmpeg_safe_subtitles.srt" in result
    assert "\\" not in result or result.count("\\") == result.count("\\:")  # only escaped colons, no raw backslashes


def test_stitch_segments_from_manifest_resolves_backslash_relative_paths(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    out_dir = work_dir / "out_segs"
    out_dir.mkdir(parents=True)

    wav_path = out_dir / "seg_0001.wav"
    AudioSegment.silent(duration=500, frame_rate=44100).export(wav_path, format="wav")

    manifest = [
        {
            "id": 1,
            "text": "hello",
            "start_ms": 0,
            "end_ms": 500,
            "wav": str(Path("work") / "out_segs" / "seg_0001.wav").replace("/", "\\"),
            "dur_target_ms": 500,
            "dur_actual_ms": 500,
            "diff_ms": 0,
            "speed_factor": 1.0,
            "content_hash": "abc",
        }
    ]

    monkeypatch.chdir(tmp_path)
    final_audio = stitch_segments_from_manifest(manifest, sample_rate=44100, gain_db=0)

    assert len(final_audio) >= 500