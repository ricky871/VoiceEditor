import shutil
import logging
from pathlib import Path
from typing import List, Union

from src.config import DEFAULT_WORK_DIR, DIRNAME_SEGMENTS

class ResourceManager:
    """
    Centralized resource manager for handling work directories, temporary files, and cleanup.
    """
    def __init__(self, work_dir: Union[str, Path] = DEFAULT_WORK_DIR, out_dir: Union[str, Path] = ""):
        self.work_dir = Path(work_dir).resolve()
        
        # Use config defaults if not provided
        if not out_dir:
            self.out_dir = self.work_dir / DIRNAME_SEGMENTS
        else:
            self.out_dir = Path(out_dir).resolve()
            
        self._temp_files: List[Path] = []
        self._ensure_output_within_workdir()

    def _ensure_output_within_workdir(self) -> None:
        """Keep out_dir inside work_dir to avoid path traversal and accidental writes outside the workspace."""
        try:
            self.out_dir.relative_to(self.work_dir)
        except ValueError:
            logging.warning(
                "Output directory %s is outside work directory %s; clamping to a safe subdirectory.",
                self.out_dir,
                self.work_dir,
            )
            self.out_dir = (self.work_dir / self.out_dir.name).resolve()

    def is_path_safe(self, path: Union[str, Path], allow_outside: bool = False) -> bool:
        """
        Check if a path is within work_dir boundary (security/safety check).
        
        Args:
            path: Path to check (can be absolute or relative)
            allow_outside: If True, paths outside work_dir are allowed (e.g., system resources)
            
        Returns:
            True if path is safe or allowed, False otherwise
        """
        if not path:
            return True
        
        path_obj = Path(path).resolve()
        try:
            path_obj.relative_to(self.work_dir)
            return True
        except ValueError:
            if allow_outside:
                return True
            logging.warning(
                "Path %s is outside work boundary %s. This may indicate a configuration error or path traversal attempt.",
                path_obj,
                self.work_dir,
            )
            return False

    def validate_output_path(self, path: Union[str, Path]) -> Path:
        """
        Validate and normalize an output path to ensure it stays within work_dir.
        
        Args:
            path: Path to validate
            
        Returns:
            Normalized absolute path
            
        Raises:
            ValueError: If path is outside work_dir or has suspicious traversal patterns
        """
        if not path:
            return None
        
        path_str = str(path)
        
        # Check for path traversal attempts (..)
        if ".." in path_str:
            raise ValueError(f"Path traversal detected in output path: {path}")
        
        path_obj = Path(path).resolve()
        
        # Ensure output path is within work_dir
        try:
            path_obj.relative_to(self.work_dir)
        except ValueError:
            raise ValueError(
                f"Output path {path_obj} is outside work directory {self.work_dir}. "
                f"Please ensure all outputs are within the work directory."
            )
        
        return path_obj

    def ensure_dirs(self):
        """Create necessary directories."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self

    def track_temp_file(self, file_path: Union[str, Path]) -> Path:
        """Register a file to be tracked for potential cleanup."""
        path = Path(file_path).resolve()
        self._temp_files.append(path)
        return path

    def cleanup(self, only_tracked: bool = True):
        """Clean up resources."""
        if only_tracked:
            for file in self._temp_files:
                try:
                    if file.exists():
                        file.unlink()
                        logging.debug(f"Cleaned up tracked file: {file}")
                except Exception as e:
                    logging.warning(f"Failed to cleanup {file}: {e}")
            self._temp_files.clear()
        else:
            # Dangerous: deletes entire folders
            logging.warning(f"Cleaning all outputs in {self.out_dir}")
            if self.out_dir.exists():
                shutil.rmtree(self.out_dir)
            self.out_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ensure_dir_of_file(file_path: Union[str, Path]) -> Path:
        """Ensure the parent directory of a file exists."""
        path = Path(file_path).parent
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_output_path(self, filename: str) -> Path:
        """Get a path within the work directory."""
        return self.work_dir / filename
