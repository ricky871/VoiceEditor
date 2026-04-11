#!/bin/bash
set -e

echo "Starting VoiceEditor Linux Installation..."

# 1. Update system and install dependencies
echo "Installing system dependencies (sudo required)..."
sudo apt-get update
# Added fonts-noto-cjk to support Chinese characters in FFmpeg/Subtitles
sudo apt-get install -y curl git ffmpeg python3 python3-pip python3-venv fonts-noto-cjk

# 2. Install UV (Standalone installer)
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv &> /dev/null
then
    echo "Installing 'uv' package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh -s -- -y
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
else
    echo "'uv' is already installed."
fi

# 3. Project Directory Setup
INSTALL_DIR="$HOME/VoiceEditor"
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Current directory is being used for setup: $(pwd)"
    INSTALL_DIR=$(pwd)
else
    cd "$INSTALL_DIR"
fi

# 4. Sync dependencies and setup environment
echo "Syncing Python dependencies and downloading models..."
# Ensure modelscope and huggingface-hub are available for the pre-download script
# We run 'uv sync' first to ensure all required libraries from pyproject.toml are available
uv sync
# Also ensure modelscope is explicitly installed as it's often a preferred mirror source but might be optional
uv pip install modelscope huggingface-hub faster-whisper transformers

# 5. Pre-warm required model caches to avoid runtime timeouts
echo "Pre-warming required Machine Learning models (Whisper, IndexTTS-2, MaskGCT, etc.)..."
HF_CACHE_DIR="$INSTALL_DIR/checkpoints/hf_cache"
mkdir -p "$HF_CACHE_DIR"

export HF_HOME="$HF_CACHE_DIR"
export HF_HUB_CACHE="$HF_CACHE_DIR"
export HUGGINGFACE_HUB_CACHE="$HF_CACHE_DIR"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="1"
export HF_HUB_ETAG_TIMEOUT="30"
export HF_HUB_DOWNLOAD_TIMEOUT="120"

uv run python - <<'PY'
import os
import sys
import time
import logging
from pathlib import Path

# Configure logging for the downloader
logging.basicConfig(level=logging.INFO, format="[cache] %(message)s")
logger = logging.getLogger("downloader")

def with_retry(title, func, retries=5, delay=5):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"Downloading: {title} (attempt {attempt}/{retries})")
            result = func()
            logger.info(f"Success: {title}")
            return result
        except Exception as exc:
            last_error = exc
            logger.warning(f"Failed: {title}: {exc}")
            if attempt < retries:
                time.sleep(delay)
    logger.error(f"Critical failure caching {title}: {last_error}")
    # We don't exit(1) here to allow other models to try downloading
    return None

def prewarm_models():
    cache_dir = os.environ.get("HF_HUB_CACHE")
    checkpoints_dir = Path("checkpoints")
    checkpoints_dir.mkdir(exist_ok=True)

    # 1. IndexTTS-2 Core Checkpoints (ModelScope preferred, HF fallback)
    def download_indextts():
        repo_id = "IndexTeam/IndexTTS-2"
        try:
            from modelscope import snapshot_download
            logger.info("Trying ModelScope for IndexTTS-2...")
            snapshot_download(repo_id, local_dir=str(checkpoints_dir))
            return True
        except Exception as e:
            logger.warning(f"ModelScope download failed, trying HuggingFace: {e}")
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo_id, local_dir=str(checkpoints_dir), cache_dir=cache_dir)
            return True

    with_retry("IndexTTS-2 Core Checkpoints", download_indextts)

    # 2. Whisper Models (Small & Medium)
    def download_whisper(model_size):
        from faster_whisper import WhisperModel
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        WhisperModel(model_size, device=device, compute_type=compute_type)
        return True

    for size in ["small", "medium"]:
        with_retry(f"Whisper Model ({size})", lambda s=size: download_whisper(s))

    # 3. Auxiliary Hub Models (MaskGCT, CAMPPlus, BigVGAN, Qwen)
    from huggingface_hub import hf_hub_download
    from transformers import SeamlessM4TFeatureExtractor, AutoModelForCausalLM

    with_retry(
        "SeamlessM4T (w2v-bert-2.0)",
        lambda: SeamlessM4TFeatureExtractor.from_pretrained("facebook/w2v-bert-2.0", cache_dir=cache_dir)
    )

    with_retry(
        "MaskGCT Semantic Codec",
        lambda: hf_hub_download(repo_id="amphion/MaskGCT", filename="semantic_codec/model.safetensors", cache_dir=cache_dir)
    )

    with_retry(
        "CAMPPlus Speaker Embedding",
        lambda: hf_hub_download(repo_id="funasr/campplus", filename="campplus_cn_common.bin", cache_dir=cache_dir)
    )

    with_retry(
        "BigVGAN Vocoder",
        lambda: hf_hub_download(repo_id="nvidia/bigvgan_v2_22khz_80band_256x", filename="bigvgan_generator.pt", cache_dir=cache_dir)
    )

    with_retry(
        "Qwen2-0.5B-Instruct (Base)",
        lambda: AutoModelForCausalLM.from_pretrained("Qwen/Qwen2-0.5B-Instruct", cache_dir=cache_dir, trust_remote_code=True)
    )

if __name__ == "__main__":
    prewarm_models()
PY

echo "-------------------------------------------------------"
echo "Installation Complete!"
echo "Project Path: $INSTALL_DIR"
echo "You can now run the GUI using: uv run main_gui.py"
echo "Or use 'deploy_service.sh' to set up auto-start."
echo "-------------------------------------------------------"
