from pathlib import Path
import pytest
from src.resource_manager import ResourceManager


def test_resource_manager_clamps_outdir_within_workdir(tmp_path):
    work_dir = tmp_path / "work"
    outside_dir = tmp_path / "outside"

    manager = ResourceManager(work_dir=work_dir, out_dir=outside_dir)

    assert manager.work_dir == work_dir.resolve()
    assert manager.out_dir.is_relative_to(manager.work_dir)


def test_resource_manager_accepts_nested_outdir(tmp_path):
    work_dir = tmp_path / "work"
    out_dir = work_dir / "out_segs"

    manager = ResourceManager(work_dir=work_dir, out_dir=out_dir)

    assert manager.out_dir == out_dir.resolve()


def test_resource_manager_is_path_safe_within_workdir(tmp_path):
    """Test that is_path_safe correctly identifies paths within work_dir."""
    work_dir = tmp_path / "work"
    safe_path = work_dir / "out_segs" / "audio.wav"
    outside_path = tmp_path / "outside" / "audio.wav"
    
    manager = ResourceManager(work_dir=work_dir)
    
    assert manager.is_path_safe(safe_path) is True
    assert manager.is_path_safe(outside_path) is False


def test_resource_manager_is_path_safe_allows_outside_when_permitted(tmp_path):
    """Test that is_path_safe respects allow_outside parameter."""
    work_dir = tmp_path / "work"
    outside_path = tmp_path / "outside" / "audio.wav"
    
    manager = ResourceManager(work_dir=work_dir)
    
    # Default: should return False
    assert manager.is_path_safe(outside_path, allow_outside=False) is False
    # With allow_outside=True: should return True
    assert manager.is_path_safe(outside_path, allow_outside=True) is True


def test_resource_manager_validate_output_path_within_boundary(tmp_path):
    """Test that validate_output_path enforces work_dir boundary."""
    work_dir = tmp_path / "work"
    safe_path = work_dir / "out_segs" / "audio.wav"
    
    manager = ResourceManager(work_dir=work_dir)
    
    # Should succeed without raising
    validated = manager.validate_output_path(safe_path)
    assert validated == safe_path.resolve()


def test_resource_manager_validate_output_path_outside_boundary_raises(tmp_path):
    """Test that validate_output_path rejects paths outside work_dir."""
    work_dir = tmp_path / "work"
    outside_path = tmp_path / "outside" / "audio.wav"
    
    manager = ResourceManager(work_dir=work_dir)
    
    with pytest.raises(ValueError, match="outside work directory"):
        manager.validate_output_path(outside_path)


def test_resource_manager_validate_output_path_rejects_traversal(tmp_path):
    """Test that validate_output_path rejects path traversal attempts."""
    work_dir = tmp_path / "work"
    
    manager = ResourceManager(work_dir=work_dir)
    
    # Path with .. should be rejected
    traversal_path = work_dir / "out_segs" / ".." / ".." / "outside.wav"
    with pytest.raises(ValueError, match="Path traversal detected"):
        manager.validate_output_path(traversal_path)