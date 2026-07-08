import sys
from pathlib import Path

# Make the flat backend modules (config, detect, ...) importable from tests/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
