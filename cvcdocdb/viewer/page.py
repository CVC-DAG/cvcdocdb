"""The viewer page as one self-contained HTML document.

:func:`build_page` inlines the styles and scripts and sets the page to ask
its parent window for data (``postMessage``) instead of the HTTP API, so it
can run in a sandboxed ``iframe`` with no network access. The notebook
widget uses it, and so can any host application that embeds the viewer
(e.g. a web app relaying the requests to its own, access-controlled API with
:func:`cvcdocdb.viewer.api.handle_api`). It needs no optional dependency.

Options shared with the HTTP server (:func:`page_attributes`):

* ``layout`` — ``"classic"`` (table, filters and graph side by side) or
  ``"light"`` (graph first: the table opens on demand, node details in a
  drawer, less chrome).
* ``load_all`` — draw the whole graph on start (up to 300 nodes).
* ``accent`` — accent colour (``#RGB`` or ``#RRGGBB``), e.g. to match the
  host application.
* ``extra_css`` — a stylesheet added after the viewer's own, so a host can
  give the page its look (fonts, colours via the ``--*`` variables, radii).
* ``language`` — interface language: ``"en"`` (default), ``"ca"`` or ``"es"``.
  The data and the API's error messages are not translated.
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Optional

__all__ = ["LANGUAGES", "LAYOUTS", "accent_style", "build_page", "extra_style", "language_meta", "page_attributes",
           "with_options"]

#: Page layouts.
LAYOUTS = ("classic", "light")
#: Interface languages (the texts are in static/i18n.js).
LANGUAGES = ("en", "ca", "es")

# The frame only runs the inlined scripts and styles: no network at all.
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"
)
_ACCENT_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_HTML_TAG = '<html lang="en">'
# What would let a stylesheet end its <style> element (or open a comment).
_STYLE_BREAKOUT_RE = re.compile(r"</|<!--")


def _static_text(name: str) -> str:
    return (resources.files(__package__) / "static").joinpath(*name.split("/")).read_text("utf-8")


def _script(code: str) -> str:
    # "</script" would end the element early; it can only occur in strings.
    return "<script>" + code.replace("</script", "<\\/script") + "</script>"


def _replace_once(page: str, old: str, new: str) -> str:
    if page.count(old) != 1:
        raise RuntimeError(f"Unexpected viewer page template: {old!r} not found exactly once")
    return page.replace(old, new)


def page_attributes(layout: str = "classic", load_all: bool = False) -> str:
    """The attributes to add to the page's ``<html>`` element.

    Raises:
        ValueError: On an unknown *layout*.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}")
    attrs = ""
    if layout != "classic":
        attrs += f' data-layout="{layout}"'
    if load_all:
        attrs += ' data-load-all="true"'
    return attrs


def accent_style(accent: Optional[str]) -> str:
    """A ``<style>`` element setting the accent colour, or ``""``.

    Raises:
        ValueError: If *accent* isn't ``#RGB`` or ``#RRGGBB``.
    """
    if accent is None:
        return ""
    if not isinstance(accent, str) or not _ACCENT_RE.match(accent):
        raise ValueError("accent must be a colour like #RGB or #RRGGBB")
    # Repeated :root outranks the dark-theme rules, so it applies in both themes.
    return f"<style>:root:root:root {{ --accent: {accent}; }}</style>"


def extra_style(extra_css: Optional[str]) -> str:
    """A ``<style>`` element with the host's *extra_css*, or ``""``.

    Raises:
        ValueError: If *extra_css* could end its ``<style>`` element.
    """
    if not extra_css:
        return ""
    if not isinstance(extra_css, str) or _STYLE_BREAKOUT_RE.search(extra_css):
        raise ValueError("extra_css must be plain CSS (no '</' or '<!--')")
    return f"<style>{extra_css}</style>"


def language_meta(language: str) -> str:
    """The ``<meta>`` element that sets the interface language, or ``""`` for English.

    It goes in ``<head>``, not on ``<html>``, whose tag the notebook widget
    rewrites.

    Raises:
        ValueError: If *language* isn't one of :data:`LANGUAGES`.
    """
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
    if language == "en":
        return ""
    return f'<meta name="cvcdocdb-language" content="{language}">'


def with_options(page: str, layout: str = "classic", load_all: bool = False, accent: Optional[str] = None,
                 extra_css: Optional[str] = None, language: str = "en") -> str:
    """*page* (the viewer's ``index.html``, plain or inlined) with the options applied."""
    attrs = page_attributes(layout, load_all)
    style = language_meta(language) + accent_style(accent) + extra_style(extra_css)
    if attrs:
        page = _replace_once(page, _HTML_TAG, _HTML_TAG[:-1] + attrs + ">")
    if style:
        page = _replace_once(page, "</head>", style + "\n</head>")
    return page


def build_page(layout: str = "classic", load_all: bool = False, accent: Optional[str] = None,
               extra_css: Optional[str] = None, language: str = "en") -> str:
    """The viewer page with its styles and scripts inlined, set to ask its
    parent window for data instead of the HTTP API.

    Args:
        layout: ``"classic"`` or ``"light"`` (see the module docs).
        load_all: Draw the whole graph on start (up to 300 nodes).
        accent: Accent colour, ``#RGB`` or ``#RRGGBB``.
        extra_css: A stylesheet added after the viewer's own (host look).
        language: Interface language, one of :data:`LANGUAGES`.

    Raises:
        ValueError: On an unknown *layout* or *language*, an invalid *accent* or *extra_css*.
    """
    page = _static_text("index.html")
    page = _replace_once(
        page,
        '<meta charset="utf-8">',
        f'<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="{_CSP}">',
    )
    page = _replace_once(
        page,
        '<link rel="stylesheet" href="static/viewer.css">',
        "<style>" + _static_text("viewer.css") + "</style>",
    )
    page = _replace_once(
        page,
        '<script src="static/vendor/cytoscape.min.js"></script>',
        _script(_static_text("vendor/cytoscape.min.js")),
    )
    for name in ("physics.js", "lod.js", "i18n.js"):
        page = _replace_once(page, f'<script src="static/{name}"></script>', _script(_static_text(name)))
    page = _replace_once(
        page,
        '<script src="static/viewer.js"></script>',
        _script("window.CVCDOCDB_BRIDGE = true;") + _script(_static_text("viewer.js")),
    )
    return with_options(page, layout=layout, load_all=load_all, accent=accent, extra_css=extra_css, language=language)
