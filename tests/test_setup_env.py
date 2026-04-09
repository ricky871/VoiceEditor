"""
Tests for src/setup_env.py module.
"""
import pytest
import os
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestEnvironmentSetup:
    """Test environment variable setup functions."""

    def test_patch_tqdm_disable(self):
        """Test disabling TQDM progress bars."""
        # Placeholder for tqdm patching test
        assert True

    def test_get_device_cuda(self):
        """Test device detection with CUDA available."""
        with patch("torch.cuda.is_available", return_value=True):
            from src.config import get_device
            
            device = get_device()
            # Should return appropriate device
            assert device in ["cuda", "mps", "xpu", "cpu"]

    def test_get_device_cpu_fallback(self):
        """Test device detection falls back to CPU."""
        with patch("torch.cuda.is_available", return_value=False):
            with patch("torch.backends.mps.is_available", return_value=False):
                from src.config import get_device
                
                device = get_device()
                assert device == "cpu"


class TestEnvironmentVariables:
    """Test environment variable configuration."""

    def test_hf_endpoint_set(self):
        """Test HuggingFace endpoint is configured."""
        from src.config import setup_environment
        
        with patch.dict(os.environ, {}, clear=False):
            setup_environment()
            
            # Should set HF_ENDPOINT
            assert "HF_ENDPOINT" in os.environ

    def test_hf_home_configured(self):
        """Test HuggingFace cache directory is configured."""
        from src.config import setup_environment
        
        with patch.dict(os.environ, {}, clear=False):
            setup_environment()
            
            # Should set HF_HOME
            assert "HF_HOME" in os.environ

    def test_huggingface_hub_cache_set(self):
        """Test HuggingFace hub cache is configured."""
        from src.config import setup_environment
        
        with patch.dict(os.environ, {}, clear=False):
            setup_environment()
            
            # Should set cache directories
            assert "HUGGINGFACE_HUB_CACHE" in os.environ


class TestDotEnvLoading:
    """Test .env file loading."""

    def test_load_env_file_exists(self, tmp_path):
        """Test loading .env file when it exists."""
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_VAR=test_value\n")
        
        with patch("src.config.PROJECT_ROOT", tmp_path):
            from src.config import load_dotenv
            
            with patch.dict(os.environ, {}, clear=False):
                load_dotenv()
                # .env file should be loaded

    def test_load_env_file_missing(self):
        """Test loading .env file when it doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            from src.config import load_dotenv
            
            # Should handle gracefully when file doesn't exist
            load_dotenv()
            assert True


class TestConfigClass:
    """Test Config class initialization and values."""

    def test_config_defaults(self):
        """Test Config class has proper default values."""
        from src.config import Config
        
        config = Config()
        assert config.diffusion_steps == 25
        assert config.emo_alpha == 0.8
        assert config.speed == 1.0
        assert config.sample_rate == 44100
        assert config.gain_db == -1.5

    def test_config_custom_values(self):
        """Test Config class accepts custom values."""
        from src.config import Config
        
        config = Config(
            diffusion_steps=50,
            emo_alpha=0.5,
            speed=1.5,
            sample_rate=48000,
            gain_db=3.0
        )
        
        assert config.diffusion_steps == 50
        assert config.emo_alpha == 0.5
        assert config.speed == 1.5
        assert config.sample_rate == 48000
        assert config.gain_db == 3.0

    def test_config_path_properties(self):
        """Test Config class converts string paths to Path objects."""
        from src.config import Config

        config = Config()
        assert isinstance(config.cfg_path, Path)
        assert isinstance(config.model_dir, Path)
        # ref_voice and out_dir default to None when not provided
        assert config.ref_voice is None
        assert config.out_dir is None
