import sys
from pathlib import Path


FUNCTION_ROOT = Path(__file__).parents[1] / "src" / "function"
sys.path.insert(0, str(FUNCTION_ROOT))
