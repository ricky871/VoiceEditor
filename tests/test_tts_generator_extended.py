"""
Integration tests for src.tts_generator and orchestration.
Testing full run logic, result management, and cancellations.
"""
import pytest
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.tts_generator import run_tts_generation, main

class TestTTSGeneratorExtended:
    @patch("src.tts_generator.setup_environment")
    @patch("src.tts_generator.Config.from_args")
    @patch("src.tts_generator.ResourceManager")
    @patch("src.tts_generator.TTSModelManager")
    @patch("src.tts_generator.SRTProcessor")
    @patch("src.tts_generator.TTSSynthesizer")
    def test_run_tts_generation_full_success(self, mock_syn, mock_srt, mock_mod, mock_res, mock_cfg, mock_env, tmp_path):
        """Test a complete successful run with video muxing."""
        # 1. Setup mocks
        config = MagicMock()
        config.out_dir = tmp_path / "out"
        config.work_dir = tmp_path / "work"
        config.ref_voice = Path("ref.wav")
        config.srt_pattern = "sub.srt"
        config.stitch = True
        config.video = Path("in.mp4")
        config.output_video = Path("out.mp4")
        config.sample_rate = 44100
        config.gain_db = 0
        config.burn_subs = False
        mock_cfg.return_value = config
        
        # Patch resolve_paths to be a no-op so it doesn't fail on mock strings
        config.resolve_paths = MagicMock()
        
        mock_mod_inst = MagicMock()
        mock_mod_inst.validate_paths.return_value = True
        mock_mod_inst.load_model.return_value = "mock_tts"
        mock_mod.return_value = mock_mod_inst
        
        mock_srt.resolve_path.return_value = Path("sub.srt")
        mock_srt.parse.return_value = [{"id": 1, "text": "Hello"}]
        mock_srt.guess_video.return_value = Path("in.mp4")
        
        mock_syn_inst = MagicMock()
        mock_syn_inst.synthesize.return_value = ([{"id": 1, "wav": "out.wav"}], 0)
        mock_syn.return_value = mock_syn_inst
        
        # Patch stitch_segments_from_manifest and mux_audio_video
        with patch("src.tts_generator.stitch_segments_from_manifest") as mock_stitch, \
             patch("src.tts_generator.mux_audio_video") as mock_mux:
            
            # Create dummy objects for video handling
            vid_path = tmp_path / "in.mp4"
            vid_path.touch()
            
            # Mock Path.exists and os.path.isdir for resources
            # Use 'src.tts_generator.Path.exists' to mock the Path class used in the module
            with patch("src.tts_generator.Path.exists", return_value=True):
                # 3. Execution
                args = MagicMock()
                args.verbose = True
                result = run_tts_generation(args)
                
                # 4. Assertions
                assert result == 0
                mock_mod_inst.load_model.assert_called_once()
                mock_syn_inst.synthesize.assert_called_once()
                mock_stitch.assert_called_once()
                mock_mux.assert_called_once()

    @patch("src.tts_generator.setup_environment")
    @patch("src.tts_generator.Config.from_args")
    @patch("src.tts_generator.ResourceManager")
    @patch("src.tts_generator.TTSModelManager")
    @patch("src.tts_generator.SRTProcessor")
    @patch("src.tts_generator.TTSSynthesizer")
    def test_run_tts_generation_cancellation(self, mock_syn, mock_srt, mock_mod, mock_res, mock_cfg, mock_env):
        """Test handling of cancellation event."""
        config = MagicMock()
        config.out_dir = Path("out")
        config.model_dir = Path("models")
        config.cfg_path = Path("config.yaml")
        config.ref_voice = Path("ref.wav")
        config.srt_pattern = "*.srt"
        mock_cfg.return_value = config
        
        mock_mod_inst = MagicMock()
        mock_mod_inst.validate_paths.return_value = True
        mock_mod_inst.load_model.return_value = "mock_tts"
        mock_mod.return_value = mock_mod_inst
        
        mock_srt.resolve_path.return_value = Path("sub.srt")
        mock_srt.parse.return_value = [{"id": 1}]
        
        mock_syn_inst = MagicMock()
        # Mock synthesizer returning cancel code
        mock_syn_inst.synthesize.return_value = ([], 130)
        mock_syn.return_value = mock_syn_inst
        
        args = MagicMock()
        result = run_tts_generation(args)
        
        assert result == 130

    @patch("src.tts_generator.setup_environment")
    @patch("src.tts_generator.Config.from_args")
    def test_run_tts_generation_missing_srt(self, mock_cfg, mock_env):
        """Test handling of missing SRT file."""
        config = MagicMock()
        config.srt_pattern = "missing.srt"
        mock_cfg.return_value = config
        
        # Patch resolve_path to raise error
        with patch("src.tts.processor.SRTProcessor.resolve_path", side_effect=FileNotFoundError):
            args = MagicMock()
            result = run_tts_generation(args)
            assert result == 1
            
    @patch("src.tts_generator.setup_environment")
    def test_main_cli_entry(self, mock_env):
        """Test main() CLI entry point call chain."""
        with patch("argparse.ArgumentParser.parse_args") as mock_args, \
             patch("src.tts_generator.run_tts_generation", return_value=42) as mock_run:
            result = main()
            assert result == 42
            mock_run.assert_called_once()
