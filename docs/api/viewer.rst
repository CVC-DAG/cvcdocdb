Graph viewer
============

:mod:`cvcdocdb.viewer` is a read-only, interactive viewer for the data in any
backend (``NetworkXGraph``, ``Neo4jGraph``, ``MemgraphGraph``, ``JenaGraph``):
node labels with counts, relation types, search by label, property filters and
free text with paging, node properties, and a graph canvas where you expand a
node's neighbours. It never writes to the graph.

It runs in two ways: inside a Jupyter notebook, or as a web page served from
your machine.

In a Jupyter notebook
---------------------

Install the ``notebook`` extra, which adds `anywidget <https://anywidget.dev>`_:

.. code-block:: bash

   pip install "cvcdocdb[notebook]"

and make :func:`~cvcdocdb.viewer.show` the last expression of a cell:

.. code-block:: python

   from cvcdocdb.viewer import show

   viewer = show(graph)             # explore from the table and the search
   viewer = show(graph, load_all=True)   # small graphs: draw every node and relation
   viewer = show(graph, language="ca")   # interface in Catalan ("es": Spanish)

It is an anywidget widget, so it can run in JupyterLab, Jupyter Notebook 7,
VS Code and Colab (so far it has been tested in JupyterLab), and it follows the
notebook's light or dark theme. No server is involved: the viewer asks the
Python kernel for each piece of data, so the graph is live but needs a running
kernel (a saved notebook reopened without one shows no data).

The viewer reads the graph when you ask it for something. After changing the
graph from Python, call ``refresh()`` to update every view of it:

.. code-block:: python

   graph.insertNode(Node(pk={"doc": "DOC-002"}, main_label="Document"))
   viewer.refresh()

Closing the graph empties it, and the viewer with it, so close it only when you
are done. :doc:`../tutorials/notebooks/interactive/weaknodes` combines the viewer
with an ``ipywidgets`` control panel that edits the graph.

In the browser
--------------

The viewer is also a small web server that needs nothing beyond the standard
library:

.. code-block:: bash

   python -m cvcdocdb.viewer path/to/graph.pkl                     # a NetworkXGraph pickle
   python -m cvcdocdb.viewer --backend neo4j --url bolt://localhost:7687 --user neo4j
   python -m cvcdocdb.viewer --backend jena --url http://localhost:3030/ds

The remote password is read from ``CVCDOCDB_PASSWORD`` (or asked for). The
server binds to ``127.0.0.1:8765`` by default (``--host``, ``--port``); use
``--token`` (or ``CVCDOCDB_VIEWER_TOKEN``) to require a token on the API, and
``--no-browser`` not to open the page, and ``--language ca`` (or ``es``) for
the interface texts in Catalan or Spanish (the data isn't translated). From
Python:

.. code-block:: python

   from cvcdocdb.viewer import serve_viewer

   serve_viewer(graph)   # http://127.0.0.1:8765/, until Ctrl+C

Security
--------

- The viewer only reads, through the store's public API, and it validates and
  caps every input. The server accepts ``GET`` requests only.
- The server binds to a loopback address by default and then only answers
  requests addressed to a loopback name. Binding another address exposes the
  data to the network: set a token.
- In a notebook, the page runs in a sandboxed ``iframe`` with no network access
  and no access to the notebook page.
- The page uses a bundled copy of cytoscape.js (MIT), so it makes no requests to
  other sites.

API
---

.. autofunction:: cvcdocdb.viewer.show

.. automodule:: cvcdocdb.viewer.notebook

.. automodule:: cvcdocdb.viewer.server

.. automodule:: cvcdocdb.viewer.data
