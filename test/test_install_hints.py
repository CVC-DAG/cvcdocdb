"""A missing optional dependency must tell the user which cvcdocdb extra to install.

Each test simulates the dependency being absent and checks the error names
the extra (``pip install "cvcdocdb[<extra>]"``), so an application knows
what to add to its own requirements.
"""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reimport_without(monkeypatch, module, *missing):
    """Import *module* afresh as if the *missing* modules weren't installed."""
    for name in missing:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.delitem(sys.modules, module, raising=False)
    return importlib.import_module(module)


@pytest.mark.unit
def test_rdf_schema_without_rdflib(monkeypatch):
    pytest.importorskip("rdflib")  # restored afterwards; only simulated missing
    with pytest.raises(ImportError, match=r'pip install "cvcdocdb\[rdf\]"'):
        _reimport_without(monkeypatch, "cvcdocdb.rdf_schema", "rdflib")


@pytest.mark.unit
def test_rdf_to_yaml_without_pyyaml(monkeypatch, tmp_path):
    pytest.importorskip("rdflib")
    from cvcdocdb import rdf_schema

    ttl = tmp_path / "o.ttl"
    ttl.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix ex: <http://example.org/> .\n"
        "ex:Doc a owl:Class .\n"
    )
    monkeypatch.setattr(rdf_schema, "yaml", None)
    with pytest.raises(ImportError, match=r'pip install "cvcdocdb\[rdf\]"'):
        rdf_schema.rdf_to_yaml(str(ttl), "demo", ontology_ns="http://example.org/")


@pytest.mark.unit
def test_generate_classes_without_pyyaml(monkeypatch):
    from cvcdocdb import schema_gen

    monkeypatch.setattr(schema_gen, "yaml", None)
    with pytest.raises(ImportError, match=r'pip install "cvcdocdb\[schema\]"'):
        schema_gen.generate_classes("labels: {}")


@pytest.mark.integration
@pytest.mark.parametrize("method, args", [("enable_vector_index", ("emb", 3))])
def test_vector_index_without_hnswlib(monkeypatch, tmp_path, method, args):
    from cvcdocdb import networkx_graph

    monkeypatch.setattr(networkx_graph, "hnswlib", None)
    graph = networkx_graph.NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
    with pytest.raises(RuntimeError, match=r'pip install "cvcdocdb\[vector\]"'):
        getattr(graph, method)(*args)


@pytest.mark.unit
def test_torch_dataloader_without_torch(monkeypatch):
    pytest.importorskip("torch")
    with pytest.raises(ImportError, match=r'pip install "cvcdocdb\[torch\]"'):
        _reimport_without(monkeypatch, "cvcdocdb.torch_dataloader", "torch", "torch.utils.data")


@pytest.mark.integration
def test_to_pyg_data_without_torch_geometric(monkeypatch, tmp_path):
    pytest.importorskip("torch")
    from cvcdocdb.networkx_graph import NetworkXGraph
    from cvcdocdb.torch_dataloader import to_pyg_data

    monkeypatch.setitem(sys.modules, "torch_geometric.data", None)
    graph = NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
    with pytest.raises(ImportError, match=r'pip install "cvcdocdb\[torch\]"'):
        to_pyg_data(graph)


@pytest.mark.unit
def test_pyg_dataloader_without_torch_geometric(monkeypatch):
    pytest.importorskip("torch")
    from cvcdocdb.torch_dataloader import PyGDataLoader

    monkeypatch.setitem(sys.modules, "torch_geometric.loader", None)
    with pytest.raises(ImportError, match=r'pip install "cvcdocdb\[torch\]"'):
        PyGDataLoader([])


@pytest.mark.integration
def test_subgraph_dataset_without_torch_geometric(monkeypatch, tmp_path):
    pytest.importorskip("torch")
    from cvcdocdb.networkx_graph import NetworkXGraph
    from cvcdocdb.torch_dataloader import SubgraphDataset

    monkeypatch.setitem(sys.modules, "torch_geometric.data", None)
    graph = NetworkXGraph(persistence_path=str(tmp_path / "g.pkl"))
    with pytest.raises(ImportError, match=r'pip install "cvcdocdb\[torch\]"'):
        next(iter(SubgraphDataset(graph)))
