"""Where the repository keeps its files."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "model"
SITE_SRC = ROOT / "site"
SPARQL_DIR = ROOT / "sparql"
