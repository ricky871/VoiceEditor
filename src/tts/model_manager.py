import os
import sys
import logging
from pathlib import Path

class TTSModelManager:
    """
    Handles the lifecycle and loading of IndexTTS2 models.
    """
    def __init__(self, cfg_path: str, model_dir: str):
        self.cfg_path = Path(cfg_path)
        self.model_dir = Path(model_dir)
        self.tts = None
    
    def setup_python_path(self):
        """Ensure index-tts package is on sys.path for imports."""
        # Project root is parent of src
        src_dir = Path(__file__).parent.parent
        project_root = src_dir.parent
        index_tts_path = project_root / "index-tts"
        if index_tts_path.exists() and str(index_tts_path) not in sys.path:
            sys.path.insert(0, str(index_tts_path))
            # logging.info(f"Added {index_tts_path} to the front of sys.path.")

    def load_model(self, use_fp16: bool = True, use_cuda: bool = True):
        """Load IndexTTS2 model with appropriate configuration."""
        self.setup_python_path()
        
        try:
            from indextts.infer_v2 import IndexTTS2
        except (ImportError, AttributeError) as exc:
            logging.error(
                "Failed to import IndexTTS2: %s. Please run 'python main.py setup' first.",
                exc,
            )
            raise

        # Auto-detect DeepSpeed
        use_ds = False
        try:
            import deepspeed
            use_ds = True
            logging.info("DeepSpeed detected. Enabling for faster inference.")
        except ImportError:
            logging.info("DeepSpeed not found. Running in standard mode.") 
            pass

        logging.info(f"Initializing IndexTTS2 with config: {self.cfg_path}")
        self.tts = IndexTTS2(
            cfg_path=str(self.cfg_path),
            model_dir=str(self.model_dir),
            use_fp16=use_fp16,
            use_cuda_kernel=False, # Custom kernels may fail on some systems
            use_deepspeed=use_ds,
        )
        return self.tts

    def validate_paths(self, ref_voice: Path):
        """Validate that all required paths exist."""
        if not self.cfg_path.exists():
            logging.error(f"Config file {self.cfg_path} not found.")
            return False
        if not self.model_dir.exists():
            logging.error(f"Model directory {self.model_dir} not found.")
            return False
        if ref_voice and not ref_voice.exists():
            logging.error(f"Reference voice {ref_voice} not found.")
            return False
        return True
