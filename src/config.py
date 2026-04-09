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

def patch_tqdm(disable: bool = True):
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
        else:
            # Restore to default if needed (less common)
            tqdm.tqdm = real_tqdm
    except ImportError:
        pass


def get_device() -> str:
    """Detect available compute device (CUDA, MPS, XPU, or CPU)."""
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return "xpu"
    return "cpu"


# Configure all major noisy loggers to ERROR level
try:
    import logging as py_logging
    # Try multiple common name variants
    for log_name in ["transformers", "diffusers", "urllib3", "huggingface_hub", "torch", "filelock", "fsspec"]:
        py_logging.getLogger(log_name).setLevel(py_logging.ERROR)
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
    os.environ.setdefault("HF_HUB_CACHE", DEFAULT_HF_HOME)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", DEFAULT_HF_HOME)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
    
# Default Paths
CHECKPOINTS_DIR = PROJECT_ROOT / "checkpoints"

# Unified Naming Constants
DEFAULT_WORK_DIR = "work"
DIRNAME_SEGMENTS = "segments"
FILENAME_STYLE_REF = "style_ref.wav"
FILENAME_MANIFEST = "segments.json"
FILENAME_MERGED_AUDIO = "audio_dubbed.wav"

WORK_DIR = PROJECT_ROOT / DEFAULT_WORK_DIR
OUT_SEGS_DIR = WORK_DIR / DIRNAME_SEGMENTS

class Config:
    """
    Centralized configuration object for the TTS pipeline.
    """
    def __init__(
        self,
        cfg_path: str = str(CHECKPOINTS_DIR / "config.yaml"),
        model_dir: str = str(CHECKPOINTS_DIR),
        ref_voice: str = "",  # Default handled in resolve_paths
        srt_pattern: str = "", # Default handled in resolve_paths
        out_dir: str = "",     # Default handled in resolve_paths
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
        burn_subs: bool = False,
        max_retries: int = 3,
        verbose: bool = False,
        force_regen: bool = False,
        work_dir: str = DEFAULT_WORK_DIR,
    ):
        self.work_dir = Path(work_dir)
        self.cfg_path = Path(cfg_path)
        self.model_dir = Path(model_dir)
        self.ref_voice = Path(ref_voice) if ref_voice else None
        self.srt_pattern = srt_pattern
        self.out_dir = Path(out_dir) if out_dir else None
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
        self.burn_subs = burn_subs
        self.max_retries = max(1, int(max_retries))
        self.verbose = verbose
        self.force_regen = force_regen
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
            burn_subs=getattr(args, "burn_subs", False),
            max_retries=getattr(args, "max_retries", 3),
            verbose=args.verbose,
            force_regen=getattr(args, "force_regen", False),
        )

    def resolve_paths(self):
        """Resolve paths to absolute paths and ensure directories exist."""
        # 1. Resolve Work Dir
        work_path = self.work_dir.resolve()
        
        # 2. Handle out_dir (segments directory)
        if not self.out_dir:
            self.out_dir = work_path / DIRNAME_SEGMENTS
        else:
            self.out_dir = self.out_dir.resolve()
            
        # 3. Handle srt_pattern
        if not self.srt_pattern:
            # Look for any .srt file in work_dir
            srts = list(work_path.glob("*.srt"))
            if srts:
                # Use the first one or the most recently modified? 
                # For now, just the glob pattern as per old behavior
                self.srt_pattern = str(work_path / "*.srt")
            else:
                self.srt_pattern = str(work_path / "*.srt")
        
        # 4. Handle ref_voice (style reference)
        if not self.ref_voice:
            # Check for new naming convention first
            new_style_ref = work_path / FILENAME_STYLE_REF
            old_voice_ref = work_path / "voice_ref.wav"
            
            if new_style_ref.exists():
                self.ref_voice = new_style_ref
            elif old_voice_ref.exists():
                logging.info(f"Found legacy reference voice: {old_voice_ref}. Using it.")
                self.ref_voice = old_voice_ref
            else:
                # Fallback to the new default name for future generation
                self.ref_voice = new_style_ref
        else:
            self.ref_voice = self.ref_voice.resolve()

        # 5. Resolve other standard paths
        self.cfg_path = self.cfg_path.resolve()
        self.model_dir = self.model_dir.resolve()
        
        # Create directories if they don't exist
        work_path.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

class SensitiveInfoFilter(logging.Filter):
    """
    Logging filter to redact sensitive information from logs.
    Protects user privacy by masking paths, usernames, and credentials.
    """
    
    # Patterns to redact
    PATTERNS = [
        # User home directory paths
        (r'[C-Z]:\\Users\\[^\s\\]+', lambda m: '<USER_HOME>'),
        (r'/home/[^\s/]+', lambda m: '<USER_HOME>'),
        (r'/Users/[^\s/]+', lambda m: '<USER_HOME>'),
        # Windows full paths (replace full path with just filename)
        (r'[C-Z]:\\(?:[^\s\\]+\\)*([^\s\\]+)', lambda m: f'<path>/{m.group(1)}'),
        # Email addresses
        (r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', lambda m: '<EMAIL>'),
        # IP addresses (but keep localhost)
        (r'(?:(?!127\.0\.0\.1)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', lambda m: '<IP>'),
        # URLs with credentials
        (r'(https?://)[^\s:]+:[^\s@]+@', lambda m: r'\1<USER>:<PASS>@'),
        # Model names with timestamps (make less identifiable)
        (r'checkpoints/[^\s/]+', lambda m: '<MODEL>'),
    ]
    
    def filter(self, record):
        """Filter log record to remove sensitive information."""
        try:
            msg = str(record.msg)
            # Apply each pattern
            for pattern, replacement in self.PATTERNS:
                msg = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
            record.msg = msg
            
            # Also filter exc_text if it exists
            if record.exc_text:
                record.exc_text = self._redact_text(record.exc_text)
        except Exception:
            pass  # If filtering fails, let the message through unfiltered
        
        return True
    
    @staticmethod
    def _redact_text(text: str) -> str:
        """Redact sensitive information from text."""
        if not text:
            return text
        for pattern, replacement in SensitiveInfoFilter.PATTERNS:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

def get_logging_config(verbose: bool = False):
    """
    Returns logging configuration with sensitive info filtering.
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

    config = {
        "level": logging.DEBUG if verbose else logging.INFO,
        "format": "%(message)s" if not verbose else "%(asctime)s %(levelname)s %(message)s",
        "datefmt": "%H:%M:%S",
    }
    
    return config

def apply_logging_filters():
    """Apply sensitive info filter to all loggers."""
    filter_obj = SensitiveInfoFilter()
    
    # Apply to root logger
    logging.root.addFilter(filter_obj)
    
    # Apply to common application loggers
    for logger_name in ['__main__', 'src', 'voiceeditor']:
        logger = logging.getLogger(logger_name)
        logger.addFilter(filter_obj)
