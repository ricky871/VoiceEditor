import pytest
from pathlib import Path
from src.tts.audio_pipeline import retime_segment_to_target
from pydub import AudioSegment

def test_retime_segment_to_target():
    # Create a 2-second silent segment (2000ms)
    # 44100Hz, 16-bit mono
    dummy_audio = AudioSegment.silent(duration=2000, frame_rate=44100)
    
    # Target 1-second (speed up by 2x)
    retimed, actual, speed = retime_segment_to_target(dummy_audio, 1000, 44100)
    
    assert speed >= 1.9 # current(2000)/target(1000) = 2.0
    # Allow some tolerance for AudioSegment/FFmpeg timing
    assert 950 <= actual <= 1050
    assert abs(len(retimed) - 1000) <= 50

    # Target 4-second (slow down by 0.5x)
    # NOTE: current(2000) < target(4000), so the implementation PADS with silence
    # and returns speed=1.0 instead of slowing down (to keep speech natural).
    retimed, actual, speed = retime_segment_to_target(dummy_audio, 4000, 44100)
    
    assert speed == 1.0 # Due to "pad shorter segments" optimization
    assert 3950 <= actual <= 4050
    assert abs(len(retimed) - 4000) <= 50
