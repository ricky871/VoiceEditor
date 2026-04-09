from pathlib import Path
from threading import Event
from types import SimpleNamespace

from pydub import AudioSegment

from src.tts.processor import TTSSynthesizer


class FakeTTS:
    def __init__(self, fail_on: str = ""):
        self.fail_on = fail_on

    def infer(self, **kwargs):
        output_path = Path(kwargs["output_path"])
        if self.fail_on and self.fail_on in output_path.name:
            raise RuntimeError("simulated synthesis failure")
        AudioSegment.silent(duration=600, frame_rate=44100).export(output_path, format="wav")


def test_synthesis_records_partial_failures_and_continues(tmp_path):
    ref_voice = tmp_path / "ref.wav"
    AudioSegment.silent(duration=1000, frame_rate=44100).export(ref_voice, format="wav")

    out_dir = tmp_path / "out_segs"
    config = SimpleNamespace(
        ref_voice=ref_voice,
        out_dir=out_dir,
        emo_text="",
        emo_audio="",
        emo_alpha=0.8,
        diffusion_steps=25,
        lang="zh",
        tokens_per_sec=150.0,
        sample_rate=44100,
        verbose=False,
        cancel_event=Event(),
        force_regen=True,
        max_retries=1,
    )

    synthesizer = TTSSynthesizer(FakeTTS(fail_on="seg_0002"), config)
    entries = [
        {"id": 1, "text": "one", "start_ms": 0, "end_ms": 500, "dur_ms": 500},
        {"id": 2, "text": "two", "start_ms": 500, "end_ms": 1000, "dur_ms": 500},
        {"id": 3, "text": "three", "start_ms": 1000, "end_ms": 1500, "dur_ms": 500},
    ]

    manifest, status = synthesizer.synthesize(entries)

    assert status == 0
    assert len(manifest) == 3
    assert any(item.get("failed") for item in manifest)
    assert any(item["id"] == 2 and item.get("failed") for item in manifest)
    assert sum(1 for item in manifest if not item.get("failed")) == 2