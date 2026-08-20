"""
TLDRGraph: Token-Efficient Hybrid Code Flow & Semantic Navigation Engine (Dynamic Multi-Layer)
"""

__version__ = "0.1.0"

# Imported for its import-time side effect: it pins graphify's output directory
# inside .tldrgraph/ before graphify can be imported and read the default. See
# tldrgraph.paths.pin_graphify_output_dir.
from . import paths as paths  # noqa: E402,F401
