"""batch/ をimportパスに通す（jquants/metrics/distribution を直接import可能に）。"""
import sys
from pathlib import Path

BATCH = Path(__file__).resolve().parents[1] / "batch"
sys.path.insert(0, str(BATCH))
