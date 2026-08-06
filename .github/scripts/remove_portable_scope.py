from pathlib import Path
path = Path('.github/PORTABLE_PREFLIGHT_SCOPE.md')
if path.exists():
    path.unlink()
