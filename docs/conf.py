"""Sphinx configuration. Build with: sphinx-build -b html docs docs/_build/html"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from estim_camming import __version__  # noqa: E402

project = "estim-camming"
author = "cb-stimmer"
copyright = "2026, cb-stimmer"
release = version = __version__

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

source_suffix = {".md": "markdown"}
myst_enable_extensions = ["colon_fence", "deflist", "fieldlist"]
myst_heading_anchors = 3

exclude_patterns = ["_build"]
autodoc_member_order = "bysource"
autodoc_typehints = "description"

try:
    import furo  # noqa: F401

    html_theme = "furo"
except ImportError:
    html_theme = "alabaster"
html_title = f"estim-camming {version}"
html_static_path = ["_static"]
