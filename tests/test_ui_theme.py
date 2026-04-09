"""
Tests for ui/theme.py module.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestThemeApplication:
    """Test theme application and UI customization."""

    def test_apply_theme_sets_colors(self):
        """Test that apply_theme sets primary, secondary, and accent colors."""
        with patch("ui.theme.ui") as mock_ui:
            from ui.theme import apply_theme
            
            apply_theme()
            
            # Should call ui.colors with color codes
            mock_ui.colors.assert_called()

    def test_apply_theme_adds_css(self):
        """Test that apply_theme adds custom CSS."""
        with patch("ui.theme.ui") as mock_ui:
            from ui.theme import apply_theme
            
            apply_theme()
            
            # Should call add_head_html for CSS
            mock_ui.add_head_html.assert_called()

    def test_apply_theme_idempotent(self):
        """Test that apply_theme can be called multiple times safely."""
        with patch("ui.theme.ui") as mock_ui:
            from ui.theme import apply_theme
            
            apply_theme()
            apply_theme()
            
            # Both calls should work without error
            assert mock_ui.colors.call_count == 2
