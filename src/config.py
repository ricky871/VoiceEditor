import os
import logging
import warnings
import re
from pathlib import Path
from typing import Optional, Any

# Projects Root
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Silently suppress the noise as early as possible
warnings.filterwarnings("ignore", module="transformers")
warnings.filterwarnings("ignore", module="torch")
warnings.filterwarnings("ignore", module="urllib3")
warnings.filterwarnings("ignore", message=".*GPT2InferenceModel.*")
warnings.filterwarnings("ignore", message=".*past_key_values.*")
warnings.filterwarnings("ignore", message=".*TypedStorage.*")
warnings.filterwarnings("ignore", message=".*directly inherit from `GenerationMixin`.*")
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

def patch_tqdm(disable=True):
    """Globally disable or enable tqdm progress bars."""
    try:
        from tqdm import tqdm as real_tqdm
        from functools import partial
        import tqdm
        # We replace the class itself OR its constructor
        if disable:
            tqdm.tqdm = partial(real_tqdm, disable=True)
            # Some libraries might import tqdm specifically, we try to cover bases
            try:
                import tqdm.notebook as tqdm_nb
                tqdm_nb.tqdm = partial(tqdm_nb.tqdm, disable=True)
            except ImportError:
                pass
    except ImportError:
        pass

# Configure all major noisy loggers to ERROR level
try:
    import logging as py_logging
    # Try multiple common name variants
    for log_name in ["transformers", "diffusers", "urllib3", "huggingface_hub", "torch"]:
        py_logging.getLogger(log_name).setLevel(py_logging.ERROR)
except Exception:
    pass

try:
    import logging as py_logging
    py_logging.getLogger("transformers").setLevel(py_logging.ERROR)
except Exception:
    pass

# HuggingFace Mirror & Cache (Defaults for China-based users)
_hf_cache_dir = PROJECT_ROOT / ".cache" / "hf"
_hf_cache_dir.mkdir(parents=True, exist_ok=True)

# Try to use existing HF_ENDPOINT or default to the most stable mirror
DEFAULT_HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
DEFAULT_HF_HOME = str(_hf_cache_dir)

def load_dotenv():
    """Simple .env loader if file exists in PROJECT_ROOT."""
    dotenv_path = PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip()
            logging.info(f"Loaded environment variables from {dotenv_path}")
        except Exception as e:
            logging.warning(f"Failed to load .env: {e}")

def setup_environment():
    """Setup default environment variables if not already set."""
    load_dotenv()
    os.environ.setdefault("HF_ENDPOINT", DEFAULT_HF_ENDPOINT)
    os.environ.setdefault("HF_HOME", DEFAULT_HF_HOME)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", DEFAULT_HF_HOME)
    
# Default Paths
CHECKPOINTS_DIR = PROJECT_ROOT / "checkpoints"
WORK_DIR = PROJECT_ROOT / "work"
OUT_SEGS_DIR = WORK_DIR / "out_segs"

class Config:
    """
    Centralized configuration object for the TTS pipeline.
    """
    def __init__(
        self,
        cfg_path: str = str(CHECKPOINTS_DIR / "config.yaml"),
        model_dir: str = str(CHECKPOINTS_DIR),
        ref_voice: str = str(WORK_DIR / "voice_ref.wav"),
        srt_pattern: str = str(WORK_DIR / "*.srt"),
        out_dir: str = str(OUT_SEGS_DIR),
        duration_mode: str = "seconds",
        tokens_per_sec: float = 150.0,
        emo_text: str = "",
        emo_audio: str = "",
        emo_alpha: float = 0.8,
        lang: str = "zh",
        speed: float = 1.0,
        stitch: bool = False,
        sample_rate: int = 44100,
        gain_db: float = -1.5,
        diffusion_steps: int = 25,
        video: str = "",
        output_video: str = "",
        verbose: bool = False,
    ):
        self.cfg_path = Path(cfg_path)
        self.model_dir = Path(model_dir)
        self.ref_voice = Path(ref_voice)
        self.srt_pattern = srt_pattern
        self.out_dir = Path(out_dir)
        self.duration_mode = duration_mode
        self.tokens_per_sec = tokens_per_sec
        self.emo_text = emo_text
        self.emo_audio = emo_audio
        self.emo_alpha = emo_alpha
        self.lang = lang
        self.speed = speed
        self.stitch = stitch
        self.sample_rate = sample_rate
        self.gain_db = gain_db
        self.diffusion_steps = diffusion_steps
        self.video = Path(video) if video else None
        self.output_video = Path(output_video) if output_video else None
        self.verbose = verbose
        self.default_model_dir = CHECKPOINTS_DIR

    @classmethod
    def from_args(cls, args: Any):
        """Factory method to create Config from argparse.Namespace."""
        return cls(
            cfg_path=args.cfg_path,
            model_dir=args.model_dir,
            ref_voice=args.ref_voice,
            srt_pattern=args.srt,
            out_dir=args.out_dir,
            duration_mode=args.duration_mode,
            tokens_per_sec=args.tokens_per_sec,
            emo_text=args.emo_text,
            emo_audio=args.emo_audio,
            emo_alpha=args.emo_alpha,
            lang=args.lang,
            speed=args.speed,
            stitch=args.stitch,
            sample_rate=args.sample_rate,
            gain_db=args.gain_db,
            diffusion_steps=getattr(args, "diffusion_steps", 25),
            video=args.video,
            output_video=args.output_video,
            verbose=args.verbose,
        )

    def resolve_paths(self):
        """Resolve paths to absolute paths and ensure directories exist."""
        self.cfg_path = self.cfg_path.resolve()
        self.model_dir = self.model_dir.resolve()
        self.out_dir = self.out_dir.resolve()

def get_logging_config(verbose: bool = False):
    """
    Returns logging configuration.
    Simplified format for cleaner terminal output.
    """
    # Suppress noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("filelock").setLevel(logging.WARNING)
    logging.getLogger("fsspec").setLevel(logging.WARNING)
    logging.getLogger("jieba").setLevel(logging.WARNING)
    logging.getLogger("numba").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.ERROR) # Suppress warnings

    return {
        "level": logging.DEBUG if verbose else logging.INFO,
        "format": "%(message)s" if not verbose else "%(asctime)s %(levelname)s %(message)s",
        "datefmt": "%H:%M:%S",
    }
