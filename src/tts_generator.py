#!/usr/bin/env python3
"""
IndexTTS2 Subtitle-Driven Zero-Shot TTS with Fine-Grained Duration Control
Rewritten to use modular architecture for better maintainability.
"""

import argparse
import sys
import logging
import time
from pathlib import Path

# Core Modules
from src.config import Config, get_logging_config, setup_environment
from src.resource_manager import ResourceManager
from src.tts.model_manager import TTSModelManager
from src.tts.audio_pipeline import stitch_segments_from_manifest, mux_audio_video
from src.tts.processor import SRTProcessor, TTSSynthesizer

def run_tts_generation(args):
    """Entry point for TTS generation using the modular components."""
    # 0. Setup Environment and Logging
    setup_environment()
    # logging.basicConfig(**get_logging_config(args.verbose)) # Already configured in main
    
    # 1. Configuration & Resource Management
    config = Config.from_args(args)
    config.resolve_paths()
    
    res_manager = ResourceManager(work_dir=config.out_dir.parent, out_dir=config.out_dir)
    res_manager.ensure_dirs()
    
    # 2. Path Validation & Subtitle Resolution
    mod_manager = TTSModelManager(config.cfg_path, config.model_dir)
    if not mod_manager.validate_paths(config.ref_voice):
        return 1
        
    try:
        srt_path = SRTProcessor.resolve_path(config.srt_pattern)
    except FileNotFoundError:
        logging.error(f"找不到字幕文件: {config.srt_pattern}")
        return 1

    entries = SRTProcessor.parse(srt_path)
    if not entries:
        logging.warning("字幕文件为空或无效。")
        return 0

    logging.debug(f"已加载 {len(entries)} 条字幕。")
    
    # 3. Model Loading
    try:
        tts = mod_manager.load_model()
        # logging.info("TTS model loaded successfully.")
    except Exception as exc:
        logging.error(f"模型加载失败: {exc}")
        return 2

    # 4. Synthesis Orchestration
    synthesizer = TTSSynthesizer(tts, config)
    start_time = time.time()
    manifest, err_code = synthesizer.synthesize(entries)
    
    if not manifest:
        logging.error("没有生成任何语音片段。")
        return 1

    # 5. Manifest Saving & Final Audio Processing
    synthesizer.save_manifest(manifest, config.out_dir)
    
    if config.stitch or config.video or config.output_video:
        final_audio = stitch_segments_from_manifest(manifest, config.sample_rate, config.gain_db)
        final_audio_path = res_manager.get_output_path("merged_audio.wav")
        # Ensure parent exists
        final_audio_path.parent.mkdir(parents=True, exist_ok=True)
        final_audio.export(str(final_audio_path), format="wav")
        logging.info(f">> 合并后的音频已保存至: {final_audio_path}")

        video_src = config.video if config.video else SRTProcessor.guess_video(srt_path)
        if video_src and video_src.exists():
            default_vid_out = res_manager.get_output_path(f"{video_src.stem}_dubbed{video_src.suffix}")
            output_vid = config.output_video if config.output_video else default_vid_out
            try:
                mux_audio_video(video_src, final_audio_path, output_vid)
                logging.info(f">> 成功合并音频到视频: {output_vid}")
            except RuntimeError:
                return 1

    elapsed = time.time() - start_time
    logging.info(f">> TTS 生成成功，耗时 {elapsed:.2f} 秒")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description="IndexTTS2 Subtitle-Driven TTS")
    parser.add_argument("--cfg_path", default="checkpoints/config.yaml")
    parser.add_argument("--model_dir", default="checkpoints")
    parser.add_argument("--ref_voice", default="work/voice_ref.wav")
    parser.add_argument("--srt", default="work/*.srt")
    parser.add_argument("--out_dir", default="work/out_segs")
    parser.add_argument("--duration_mode", choices=["seconds", "tokens"], default="seconds")
    parser.add_argument("--tokens_per_sec", type=float, default=150.0)
    parser.add_argument("--emo_text", default="")
    parser.add_argument("--emo_audio", default="")
    parser.add_argument("--emo_alpha", type=float, default=0.8)
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--stitch", action="store_true")
    parser.add_argument("--sample_rate", type=int, default=44100)
    parser.add_argument("--gain_db", type=float, default=-1.5)
    parser.add_argument("--diffusion_steps", type=int, default=25)
    parser.add_argument("--video", default="")
    parser.add_argument("--output_video", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return run_tts_generation(args)

if __name__ == "__main__":
    # Ensure project root is in sys.path when running as script
    sys.path.append(str(Path(__file__).parent.parent.resolve()))
    sys.exit(main())
