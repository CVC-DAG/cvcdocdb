RDF / OWL ontology conversion
==============================

The :mod:`cvcdocdb.rdf_schema` module provides the full pipeline for converting
RDF/OWL ontologies into CVCDocDB YAML schemas and Python entity classes.

Supported input formats
-----------------------

Turtle (``.ttl``), RDF/XML (``.rdf``), N-Triples (``.nt``), N-Quads (``.nq``),
TriX (``.trix``), Trig (``.trig``), and JSON-LD (``.jsonld``).

OWL constructs mapped to CVCDocDB
---------------------------------

+--------------------------+------------------------------------------+
| RDF / OWL construct      | CVCDocDB mapping                         |
+--------------------------+------------------------------------------+
| ``owl:Class``            | Node label                               |
+--------------------------+------------------------------------------+
| ``rdfs:subClassOf``      | ``WeakNode`` hierarchy (parent)          |
+--------------------------+------------------------------------------+
| ``owl:DatatypeProperty`` | Node properties (``rdfs:domain``)        |
+--------------------------+------------------------------------------+
| ``owl:ObjectProperty``   | Relationships (``rdfs:domain``/``range``)|
+--------------------------+------------------------------------------+
| ``owl:hasKey``           | Primary key fields                       |
+--------------------------+------------------------------------------+
| ``owl:uniqueIdentity``   | Unique identifier fields                 |
+--------------------------+------------------------------------------+
| ``rdfs:range``           | Relationship destination label           |
+--------------------------+------------------------------------------+
| ``rdfs:comment``         | Class docstring                          |
+--------------------------+------------------------------------------+

Usage examples
--------------

Download and convert in one step::

    from cvcdocdb.rdf_schema import download_ontology_and_convert

    output_path = download_ontology_and_convert(
        "https://raw.githubusercontent.com/ICA-EGAD/RiC-O/master/ontology/current-version/RiC-O_1-1.rdf",
        "rico",
        output_dir="cvcdocdb/"
    )
    # Generates cvcdocdb/rico_entities.py

Step by step::

    from cvcdocdb.rdf_schema import download_ontology, rdf_to_yaml
    from cvcdocdb.schema_gen import generate_classes

    # 1. Download
    ont_path = download_ontology(url, output_dir="ontologies/")

    # 2. Convert RDF -> YAML
    yaml_str = rdf_to_yaml(ont_path, "my_db")

    # 3. Generate Python classes
    py_source = generate_classes(yaml_str)

    # 4. Write file
    with open("cvcdocdb/entities_my_db.py", "w") as f:
        f.write(py_source)

.. automodule:: cvcdocdb.rdf_schema
   :members:
   :member-order: bysource
   :show-inheritance:
