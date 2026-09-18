# Document Representation Models

Archives, libraries, and document collections are rarely flat files: documents have internal structure -- pages, sections, regions, entities -- and rich relationships to other documents, people, places, and events. A **Document Representation Model (DRM)** captures this structure as a graph, where nodes stand for the objects that make up (or are described by) a document, and typed edges capture how those objects relate to, contain, or depend on one another. This graph-first view makes document content queryable, composable, and reusable across archival, historical, and document-analysis workflows.

## CVCDocDB

**CVCDocDB** is a Python library developed by the Document Analysis Group (DAG) at the [Centre de Visió per Computador](https://www.cvc.uab.es/) (CVC), within the framework of the **SUKIDI** project, to represent the contents of documents according to a Document Representation Model. It offers a graph-based API with two interchangeable backends -- a persistent Neo4j store and an in-memory NetworkX store for testing and tutorials -- together with semantic entity definitions, WeakNode hierarchies with cascade delete, foreign key validation, vector search, and ready-to-run example datasets for getting started quickly.

## Features

- **Two backends**: Full Neo4j integration (`Neo4jGraph`) or in-memory NetworkX (`NetworkXGraph`) for testing and tutorials
- **WeakNode hierarchy**: Child entities with composite primary keys and automatic cascade delete through parent-child edges
- **ON DELETE strategies**: CASCADE, RESTRICT, SET NULL -- choose the deletion semantics that fit your use case
- **Semantic entities**: Domain-specific node types such as `IndividuPadro`, `LlocPadro`, and `Fotografia`
- **FK validation**: Foreign key constraints on relations prevent dangling references
- **Query and filtering**: Secondary index on scalar properties, multi-filter search with intersection/union, debug snapshots
- **Vector search (NetworkX only)**: HNSW-based ANN indexing on node properties with `cosine`, `l2`, and `ip` distance spaces
- **RDF/OWL ontology conversion**: Generate Python entity classes from RDF/OWL ontologies (RiC-O, etc.)

## Installation

Install from PyPI:

```bash
pip install cvcdocdb
```

Or install from source in development mode:

```bash
git clone https://github.com/CVC-DAG/cvcdocdb.git
cd cvcdocdb
pip install -e .
```

Register the recommended Jupyter kernel for tutorials:

```bash
python -m ipykernel install --user --name cvcdocdb --display-name "Python (cvcdocdb)"
```

## Quick Start

```python
from cvcdocdb import NetworkXGraph, Node, WeakNode

# In-memory backend -- no database required
graph = NetworkXGraph()

# Create a document hierarchy
doc = Node(pk={"doc": "DOC-001"}, main_label="Document")
graph.insertNode(doc)

section = WeakNode(parent=doc, pk={"section": 1}, main_label="Section")
graph.insertNode(section, insert_parent=True)

page = WeakNode(parent=section, pk={"page": 1}, main_label="Page")
graph.insertNode(page, insert_parent=True)

# Query the graph
print("Nodes:", graph.get_node_ids())
print("Edges:", graph.get_edges())
graph.close()
```

## Tutorial Notebooks

Runnable Jupyter notebooks in `docs/tutorials/notebooks/`. Each notebook installs the package automatically from the latest release when run.

You can also view them rendered in the [hosted documentation](https://cvc-dag.github.io/cvcdocdb/).

### Getting Started

- `intro_basics` -- Minimal end-to-end workflow: insert nodes, create WeakNode hierarchies
- `querying_and_filtering` -- Query operations: `get_node()`, `find_nodes()`, property filtering

### Interactive Demos

- `weaknodes_interactive` -- Build hierarchies with an interactive widget panel
- `vector_search` -- HNSW vector indexing and nearest-neighbor search
- `delete_strategies` -- Compare CASCADE, RESTRICT, SET NULL strategies

### Datasets

- `karate_club` -- Zachary Karate Club (34 members)
- `movies` -- Movie-domain graph (actors, genres, films)
- `game_of_thrones` -- Character-house graph
- `bibliography_openalex` -- OpenAlex bibliographic references with citations

### Ontologies

- `generating_classes_from_owl` -- Generate Python entity classes from RDF/OWL ontologies

## RDF/OWL Ontology Conversion

Generate Python entity classes from RDF/OWL ontologies in one step:

```python
from cvcdocdb.rdf_schema import download_ontology_and_convert

# Downloads, converts to YAML, and generates Python classes
output_path = download_ontology_and_convert(
    "https://raw.githubusercontent.com/ICA-EGAD/RiC-O/master/ontology/current-version/RiC-O_1-1.rdf",
    "rico",
    output_dir="cvcdocdb/"
)
# Generates cvcdocdb/rico_entities.py (677 classes from RiC-O)
```

Step by step:

```python
from cvcdocdb.rdf_schema import download_ontology, rdf_to_yaml
from cvcdocdb.schema_gen import generate_classes

# 1. Download ontology
ont_path = download_ontology(url, output_dir="ontologies/")

# 2. Convert RDF to YAML DRM
yaml_str = rdf_to_yaml(ont_path, "my_db")

# 3. Generate Python classes
py_source = generate_classes(yaml_str)

# 4. Write file
with open("cvcdocdb/entities_my_db.py", "w") as f:
    f.write(py_source)
```

The pipeline maps OWL constructs to DRM:

- `owl:Class` -- Node label
- `rdfs:subClassOf` -- `WeakNode` hierarchy (parent)
- `owl:DatatypeProperty` -- Node properties
- `owl:ObjectProperty` -- Relationships
- `owl:hasKey` -- Primary key fields
- `rdfs:comment` -- Class docstring

## Example Dataset Loaders (cvcdocdb.exemples)

The package includes ready-to-run loaders for common graph domains:

- `cvcdocdb.exemples.networkx_karate` -- Karate Club graph (NetworkX classic)
- `cvcdocdb.exemples.networkx_bibliografia` -- Bibliographic references from OpenAlex
- `cvcdocdb.exemples.neo4j_movies` -- Movie-domain graph
- `cvcdocdb.exemples.neo4j_got` -- Game of Thrones character-house graph

### Command-line loader

```bash
python -m cvcdocdb.exemples --dataset karate --backend networkx
python -m cvcdocdb.exemples --dataset all --backend both --quiet
```

### Programmatic usage

```python
from cvcdocdb import NetworkXGraph
from cvcdocdb.exemples import load_karate_club, load_bibliografia_openalex

graph = NetworkXGraph()
print(load_karate_club(graph))
print(load_bibliografia_openalex(graph, query="graph database", per_page=15))
graph.close()
```

## Configuration

CVCDocDB uses environment variables for Neo4j connections. Multiple targets are supported via the `NEO4J_TARGET` selector:

```bash
# Default target
export NEO4J_DEV_URL=bolt://dev-host:7687
export NEO4J_DEV_USER=neo4j
export NEO4J_DEV_PASSWORD=your_dev_password
export NEO4J_DEV_DATABASE=neo4j

# Custom target
export NEO4J_TARGET=LOCAL
export NEO4J_LOCAL_URL=bolt://localhost:7687
export NEO4J_LOCAL_USER=neo4j
export NEO4J_LOCAL_PASSWORD=your_password
export NEO4J_LOCAL_DATABASE=neo4j
```

## Running Tests

```bash
python -m pytest test/ -v
```

Three test levels:

- **Unit** (`-m unit`) -- fast, no graph store
- **Integration** (`-m integration`) -- NetworkXGraph (in-memory)
- **Neo4j** (`-m slow`) -- requires a real Neo4j connection; auto-skipped otherwise

Skip Neo4j tests: `pytest test/ -v -m "not slow"`

## Documentation

- **Hosted docs**: https://cvc-dag.github.io/cvcdocdb/
- **Source docs**: `docs/` -- Sphinx documentation source

Generate HTML docs with Sphinx:

```bash
cd docs
sphinx-build -b html . _build/html
```

## Authors and Contributors

- Oriol Ramos Terrades
- Jialuo Chen
- Adrià Molina

## Acknowledgements

This work has been partially supported by the Spanish project PID2021-126808OB-I00, Ministerio de Ciencia e Innovación, the Departament de Cultura of the Generalitat de Catalunya, and the CERCA Program / Generalitat de Catalunya. Adrià Molina is funded with the PRE2022-101575 grant provided by MCIN / AEI / 10.13039 / 501100011033 and by the European Social Fund (FSE+).
