Backend-to-backend migration
=============================

:mod:`cvcdocdb.migration` copies an entire graph — nodes, then edges, then
vector indexes — from one :class:`~cvcdocdb.graph_store.GraphStore` into
another (e.g. ``NetworkXGraph`` → ``Neo4jGraph`` or the reverse), using only
the common ``GraphStore`` interface. It doesn't know anything about
``WeakNode``, ``Individu``/``be_value_properties``, or any other Python-level
entity class, so it works between any two backend implementations, current
or future.

Quick-start
-----------

.. code-block:: python

    from cvcdocdb import NetworkXGraph, Neo4jGraph
    from cvcdocdb.migration import migrate

    source = NetworkXGraph("archive.pkl")
    target = Neo4jGraph("bolt://localhost:7687", "neo4j", "secret")
    stats = migrate(source, target)
    print(stats)  # MigrationStats(nodes_migrated=120, edges_migrated=340, ...)

Notes
-----

- Nodes are matched between source and target by ``(main_label, pk)``. Some
  backends (currently ``Neo4jGraph``) don't persist which properties form a
  node's primary key — when that happens, every property on the node is used
  as its pk instead, so the node is still faithfully reproduced.
- ``label_filter``/``property_filter`` accept the same MongoDB-style syntax
  as :class:`~cvcdocdb.torch_dataloader.GraphDataset`.
- Reads/writes are batched (``chunk_size``), with a faster round-trip path
  when the source/target is a ``Neo4jGraph``.
- ``on_error="skip"`` allows a best-effort migration of a large, possibly
  messy graph instead of aborting on the first error.

See also :mod:`cvcdocdb.graph_store` for the interface this module relies
on, and :mod:`cvcdocdb.schema_gen` to generate Python entity classes from
either side's schema after migrating.

.. automodule:: cvcdocdb.migration
   :members:
   :member-order: bysource
   :show-inheritance:
