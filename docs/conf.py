# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

from pathlib import Path

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

# Read straight from VERSION.txt rather than importing strata: docs must build
# without the package installed, and a literal here would be a second source
# of truth that silently disagrees with the real version.
release = (Path(__file__).resolve().parent.parent / "VERSION.txt").read_text(encoding="utf-8").strip()

project = "strata"
copyright = "2026, Huybrechts XYZ"
author = "Vincent Huybrechts"

# Project URLs
project_urls = {
    "Homepage": "https://github.com/huybrechtsxyz/strata",
}

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx_toggleprompt",
    "sphinx_copybutton",
    "myst_parser",
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
source_suffix = [".rst", ".md"]

# The master toctree document.
master_doc = "index"

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# These patterns also effect html_static_path and html_extra_path
# Convention: prefix scratch/temp docs with _ (e.g. _draft-notes.md) to
# auto-exclude them from both the Sphinx build and the index-coverage check.
exclude_patterns = ["_build", "_*", "Thumbs.db", ".DS_Store", "decisions"]

# Generate Sphinx-compatible anchors for headings (h1-h3) so that
# Markdown ToC links like [Key Features](#key-features) resolve correctly.
myst_heading_anchors = 3

# Links pointing outside the Sphinx source root cannot be resolved as
# cross-references. Suppress rather than error.
suppress_warnings = ["myst.xref_missing"]

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "sphinx"

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = False

# -- Options for HTML output -------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
html_theme = "sphinx_rtd_theme"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# -- Options for HTMLHelp output ---------------------------------------

# Output file base name for HTML help builder.
htmlhelp_basename = "docs"

# -- Options for Napoleon ----------------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# -- Also document __init__ methods

autoclass_content = "both"
autosummary_generate = True  # Turn on sphinx.ext.autosummary

# -- Toggleprompt / copybutton overlap

toggleprompt_offset_right = 35
