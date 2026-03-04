#!/bin/bash
set -e

echo "Starting VoiceEditor Linux Installation..."

# 1. Update system and install dependencies
echo "Installing system dependencies (sudo required)..."
sudo apt-get update
sudo apt-get install -y curl git ffmpeg python3 python3-pip python3-venv

# 2. Install UV (Standalone installer)
if ! command -v uv &> /dev/null
then
    echo "Installing 'uv' package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
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
# This will run src/setup_env.py as defined in main.py setup
uv run main.py setup

# 5. Pre-warm required HuggingFace caches to avoid runtime timeouts
echo "Pre-warming required HuggingFace cache files..."
HF_CACHE_DIR="$INSTALL_DIR/checkpoints/hf_cache"
mkdir -p "$HF_CACHE_DIR"

export HF_HOME="$HF_CACHE_DIR"
export HF_HUB_CACHE="$HF_CACHE_DIR"
export HUGGINGFACE_HUB_CACHE="$HF_CACHE_DIR"
export HF_HUB_DISABLE_XET="1"
export HF_HUB_ETAG_TIMEOUT="30"
export HF_HUB_DOWNLOAD_TIMEOUT="120"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

uv run python - <<'PY'
import os
import time

from huggingface_hub import hf_hub_download
from transformers import SeamlessM4TFeatureExtractor

cache_dir = os.environ.get("HF_HUB_CACHE")


def with_retry(title, func, retries=5, delay=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            print(f"[cache] downloading: {title} (attempt {attempt}/{retries})")
            result = func()
            print(f"[cache] ready: {title}")
            return result
        except Exception as exc:
            last_error = exc
            print(f"[cache] failed: {title}: {exc}")
            if attempt < retries:
                time.sleep(delay)
    raise RuntimeError(f"Failed to cache {title}: {last_error}")


with_retry(
    "facebook/w2v-bert-2.0",
    lambda: SeamlessM4TFeatureExtractor.from_pretrained(
        "facebook/w2v-bert-2.0",
        cache_dir=cache_dir,
    ),
)

with_retry(
    "amphion/MaskGCT/semantic_codec/model.safetensors",
    lambda: hf_hub_download(
        repo_id="amphion/MaskGCT",
        filename="semantic_codec/model.safetensors",
        cache_dir=cache_dir,
    ),
)

with_retry(
    "funasr/campplus/campplus_cn_common.bin",
    lambda: hf_hub_download(
        repo_id="funasr/campplus",
        filename="campplus_cn_common.bin",
        cache_dir=cache_dir,
    ),
)

with_retry(
    "nvidia/bigvgan_v2_22khz_80band_256x/config.json",
    lambda: hf_hub_download(
        repo_id="nvidia/bigvgan_v2_22khz_80band_256x",
        filename="config.json",
        cache_dir=cache_dir,
    ),
)

with_retry(
    "nvidia/bigvgan_v2_22khz_80band_256x/bigvgan_generator.pt",
    lambda: hf_hub_download(
        repo_id="nvidia/bigvgan_v2_22khz_80band_256x",
        filename="bigvgan_generator.pt",
        cache_dir=cache_dir,
    ),
)

print(f"[cache] all required files are ready in: {cache_dir}")
PY

echo "-------------------------------------------------------"
echo "Installation Complete!"
echo "Project Path: $INSTALL_DIR"
echo "You can now run the GUI using: uv run main_gui.py"
echo "Or use 'deploy_service.sh' to set up auto-start."
echo "-------------------------------------------------------"
