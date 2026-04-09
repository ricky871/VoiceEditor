from argparse import Namespace
from pathlib import Path

from pydub import AudioSegment

import src.tts_generator as tts_generator


class DummyModelManager:
    def __init__(self, cfg_path, model_dir):
        self.cfg_path = Path(cfg_path)
        self.model_dir = Path(model_dir)

    def validate_paths(self, ref_voice):
        return True

    def load_model(self):
        return object()


class DummySynthesizer:
    def __init__(self, tts, config):
        self.tts = tts
        self.config = config

    def synthesize(self, entries):
        manifest = [
            {
                "id": 1,
                "text": entries[0]["text"],
                "start_ms": 0,
                "end_ms": 100,
                "wav": "seg_0001.wav",
                "dur_target_ms": 100,
                "dur_actual_ms": 100,
                "diff_ms": 0,
                "speed_factor": 1.0,
                "content_hash": "hash",
            }
        ]
        return manifest, 0


def test_cli_args_flow_into_config_and_mux(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("dummy", encoding="utf-8")
    ref_voice = tmp_path / "ref.wav"
    AudioSegment.silent(duration=1000, frame_rate=44100).export(ref_voice, format="wav")
    video = tmp_path / "input.mp4"
    video.write_text("video", encoding="utf-8")
    srt = tmp_path / "input.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,100\nhello\n", encoding="utf-8")

    captured = {}

    def fake_stitch_segments_from_manifest(manifest, sample_rate, gain_db, manifest_dir=None):
        captured["manifest"] = manifest
        captured["sample_rate"] = sample_rate
        captured["gain_db"] = gain_db
        captured["manifest_dir"] = manifest_dir
        return AudioSegment.silent(duration=100, frame_rate=sample_rate)

    def fake_mux_audio_video(video_path, audio_path, output_path, srt_path=None):
        captured["mux_video"] = str(video_path)
        captured["mux_audio"] = str(audio_path)
        captured["mux_output"] = str(output_path)
        captured["mux_srt"] = str(srt_path) if srt_path else None

    monkeypatch.setattr(tts_generator, "TTSModelManager", DummyModelManager)
    monkeypatch.setattr(tts_generator, "TTSSynthesizer", DummySynthesizer)
    monkeypatch.setattr(tts_generator, "stitch_segments_from_manifest", fake_stitch_segments_from_manifest)
    monkeypatch.setattr(tts_generator, "mux_audio_video", fake_mux_audio_video)

    args = Namespace(
        cfg_path=str(cfg_path),
        model_dir=str(tmp_path),
        ref_voice=str(ref_voice),
        srt=str(srt),
        out_dir=str(tmp_path / "out_segs"),
        duration_mode="seconds",
        tokens_per_sec=150.0,
        emo_text="",
        emo_audio="",
        emo_alpha=0.8,
        lang="zh",
        speed=1.0,
        stitch=True,
        sample_rate=44100,
        gain_db=-1.5,
        diffusion_steps=25,
        max_retries=7,
        video=str(video),
        output_video=str(tmp_path / "dubbed.mp4"),
        burn_subs=True,
        verbose=False,
        cancel_event=None,
        force_regen=False,
    )

    status = tts_generator.run_tts_generation(args)

    assert status == 0
    assert captured["sample_rate"] == 44100
    assert captured["gain_db"] == -1.5
    assert captured["mux_srt"] == str(srt)
    assert captured["mux_video"] == str(video)
    assert captured["manifest"][0]["id"] == 1