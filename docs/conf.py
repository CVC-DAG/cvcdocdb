# Configuration file for the Sphinx documentation builder.
#
# For the full list of built configurations, see the Sphinx docs:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import shutil
import sys

# Add the project root to the path so Sphinx can find the cvcdocdb package.
sys.path.insert(0, os.path.abspath(os.path.join("..")))

# Check if pandoc is available (required by nbsphinx for notebooks)
_has_pandoc = shutil.which("pandoc") is not None

# -- Project information -----------------------------------------------------
project = "cvcdocdb"
copyright = "2025, Oriol Ramos Terrades, Jialuo Chen, Adrià Molina"
author = "Oriol Ramos Terrades, Jialuo Chen, Adrià Molina"

# The full version, including alpha/beta/rc tags
try:
    from cvcdocdb import __version__ as cvcdocdb_version  # noqa: E402
except ImportError:
    cvcdocdb_version = None

release = cvcdocdb_version if cvcdocdb_version else "0.0.0.dev0"
version = release

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",       # Auto-generate docs from docstrings
    "sphinx.ext.napoleon",      # Google and NumPy style docstring support
    "sphinx.ext.viewcode",      # Add links to highlighted source code
    "sphinx.ext.intersphinx",   # Link to other projects' documentation
    # "nbsphinx" — loaded conditionally below (requires pandoc)
    # "sphinx_autodoc_typehints" — disabled: crashes on dict-inherited descriptors
]

# nbsphinx requires pandoc; skip it in CI environments without pandoc
if _has_pandoc:
    extensions.append("nbsphinx")
else:
    nbsphinx_enabled = False
    # Warn but continue — notebooks will not be rendered
    import warnings
    warnings.warn(
        "nbsphinx disabled: pandoc not found. "
        "Install pandoc to render notebooks in documentation. "
        "See https://pandoc.org/installing.html",
        stacklevel=2,
    )

# Napoleon settings for Google/NumPy style docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = True
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# Autodoc settings
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": (
        "__weakref__, pk, keys, values, items, __getitem__,"
        " __setitem__, __delitem__, __contains__, __len__,"
        " __iter__, __eq__, __hash__, copy, clear,"
        " pop, popitem, update, fromkeys, get"
    ),
}
autodoc_typehints = "description"  # Show types in descriptions, not signatures
autodoc_typehints_description_target = "all"

# Intersphinx mapping
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "networkx": ("https://networkx.org/documentation/stable/", None),
}

# Templates path
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- Options for HTML output -------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

# Theme options
html_theme_options = {
    "prev_next_buttons_location": "bottom",
    "style_external_links": False,
    "collapse_navigation": True,
    "sticky_navigation": True,
    "navigation_depth": 4,
    "includehidden": True,
    "titles_only": False,
}

# -- nbsphinx ---------------------------------------------------------------
# Execute notebooks manually; docs build should render existing outputs as-is.
nbsphinx_execute = "never"

# Notebook link settings (can be overridden in CI/local env):
# - DRM_DOCS_GITHUB_REPO, e.g. CVC-DAG/cvcdocdb
# - DRM_DOCS_GITHUB_REF, e.g. main, dev, feature/branch
# - DRM_DOCS_LOCAL_JUPYTER_BASE, e.g. http://127.0.0.1:8888
# - DRM_DOCS_LOCAL_NOTEBOOK_PREFIX, e.g. docs
docs_github_repo = os.getenv("DRM_DOCS_GITHUB_REPO", "CVC-DAG/cvcdocdb").strip("/")
docs_github_ref = os.getenv("DRM_DOCS_GITHUB_REF", "main").strip()
docs_local_jupyter_base = os.getenv("DRM_DOCS_LOCAL_JUPYTER_BASE", "http://127.0.0.1:8888").rstrip("/")
docs_local_notebook_prefix = os.getenv("DRM_DOCS_LOCAL_NOTEBOOK_PREFIX", "docs").strip("/")

if docs_local_notebook_prefix:
    local_notebook_href = (
        f"{docs_local_jupyter_base}/notebooks/"
        f"{docs_local_notebook_prefix}/{{{{ env.docname }}}}.ipynb"
    )
else:
    local_notebook_href = f"{docs_local_jupyter_base}/notebooks/{{{{ env.docname }}}}.ipynb"

colab_href = (
    f"https://colab.research.google.com/github/{docs_github_repo}/"
    f"blob/{docs_github_ref}/docs/{{{{ env.docname }}}}.ipynb"
)

kaggle_href = (
    f"https://www.kaggle.com/kernels/welcome?src="
    f"https://raw.githubusercontent.com/{docs_github_repo}/"
    f"{docs_github_ref}/docs/{{{{ env.docname }}}}.ipynb"
)

# Add a Colab badge at the top of each rendered notebook page.
nbsphinx_prolog = f"""
.. raw:: html

    <div style="margin: 0.5rem 0 1rem 0; display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center;">
      <a href="{colab_href}" target="_blank" rel="noopener noreferrer">
        <img alt="Open in Colab" src="https://colab.research.google.com/assets/colab-badge.svg" />
      </a>
      <a href="{kaggle_href}" target="_blank" rel="noopener noreferrer">
        <img alt="Open in Kaggle" src="https://kaggle.com/static/images/open-in-kaggle.svg" />
      </a>
      <a href="{local_notebook_href}" target="_blank" rel="noopener noreferrer" style="font-size: 0.9rem; padding: 0.3rem 0.6rem; border: 1px solid #888; border-radius: 4px; text-decoration: none;">
        Open in Local Jupyter
      </a>
    </div>
"""

# -- Options for LaTeX output ------------------------------------------------
latex_elements = {}
latex_documents = [
    (
        "index",
        "cvcdocdb.tex",
        "cvcdocdb Documentation",
        "Oriol Ramos Terrades, Jialuo Chen, Adrià Molina",
        "manual",
    ),
]

# Skip sphinx-autodoc-typehints processing for properties and descriptors
# that cause "'getset_descriptor' object is not iterable" errors.
def _autodoc_skip_member(app, what, name, obj, skip, options):
    """Skip private members, properties and built-in descriptors from autodoc."""
    # Keep __init__, skip everything else starting with _
    if name.startswith("_") and name != "__init__":
        return True
    # Skip property descriptors that crash sphinx-autodoc-typehints
    if isinstance(obj, property) or type(obj).__name__ in (
        "getset_descriptor", "member_descriptor",
    ):
        return True
    return None  # let default logic decide

def _skip_properties_docstring(app, what, name, obj, options, lines):
    """Remove auto-generated :ivar: lines for properties."""
    if what in ("attribute", "property") or isinstance(obj, property):
        del lines[:]

def setup(app):
    app.connect("autodoc-skip-member", _autodoc_skip_member)
    app.connect("autodoc-process-docstring", _skip_properties_docstring)

# -- Options for manual page output ------------------------------------------
man_pages = [
    (
        "index",
        "cvcdocdb",
        "cvcdocdb Documentation",
        ["Oriol Ramos Terrades, Jialuo Chen, Adrià Molina"],
        1,
    ),
]
