"""Root conftest — ensures src/ is on PYTHONPATH for test discovery."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
