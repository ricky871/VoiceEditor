from nicegui import ui


def apply_theme() -> None:
    ui.colors(primary="#2563eb", secondary="#0f172a", accent="#10b981")
    ui.add_head_html("""
    <style>
      .app-shell { max-width: 1500px; margin: 0 auto; }
      .mono-textarea textarea { font-family: Consolas, Menlo, monospace; }
    </style>
    """)