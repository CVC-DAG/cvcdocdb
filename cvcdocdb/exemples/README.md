# DRM exemples

Aquest directori conté loaders de datasets per tutorials:

- `networkx_karate.py`: Zachary Karate Club (NetworkX classic)
- `networkx_bibliografia.py`: papers/citacions/autoria des d'OpenAlex
- `neo4j_movies.py`: dataset de domini de pel·lícules (estil Neo4j Movies)
- `neo4j_got.py`: dataset de personatges i cases de Game of Thrones

## Ús ràpid

```python
from cvcdocdb import NetworkXGraph
from cvcdocdb.exemples import load_karate_club, load_bibliografia_openalex

graph = NetworkXGraph()
print(load_karate_club(graph))
print(load_bibliografia_openalex(graph, query="graph database", per_page=15))
graph.close()
```

Per Neo4j, passa una instància de `Neo4jGraph` als loaders `load_movies_sample` i `load_got_characters`.

