PyTorch / PyTorch Geometric dataloader
=======================================

:mod:`cvcdocdb.torch_dataloader` streams a :class:`~cvcdocdb.graph_store.GraphStore`
graph into PyTorch/PyTorch Geometric tensors without materializing the whole
graph in memory, for use with node embedding models (e.g. ``MetaPath2Vec``)
and link-prediction pipelines.

Quick-start
-----------

.. code-block:: python

    from cvcdocdb import NetworkXGraph
    from cvcdocdb.torch_dataloader import GraphDataset, to_hetero_edge_index_dict

    graph = NetworkXGraph("bibliography.pkl")
    dataset = GraphDataset(graph)
    edge_index_dict = to_hetero_edge_index_dict(dataset)

Notes
-----

- ``GraphDataset``/``SubgraphDataset`` stream nodes and edges in chunks
  rather than loading the entire graph into memory at once.
- ``label_filter``/``property_filter`` accept the same MongoDB-style syntax
  as :func:`cvcdocdb.migration.migrate`.
- ``split_edges_by_node_property`` partitions edges into N groups based on a
  scalar node property (e.g. train/test splits by a ``num`` threshold, or
  more than two groups), and works for any property, not just numeric ones.
- ``edge_embeddings`` derives an edge-level embedding from its endpoint node
  embeddings for use in link-prediction models.

See also the "PyTorch / PyTorch Geometric dataloader" tutorial notebook for a
worked example on the bibliographic dataset (MetaPath2Vec training and link
prediction).

.. automodule:: cvcdocdb.torch_dataloader
   :members:
   :member-order: bysource
   :show-inheritance:
