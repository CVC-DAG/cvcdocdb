"""Core data structures: Node, Relation, WeakNode, WeakRelation.

These classes represent the graph primitives used by both Neo4jGraph
and NetworkXGraph.  Nodes carry a primary key (``pk``), a main label,
optional alternative labels, and optional parent relationships for
WeakNode hierarchies.  Relations connect two nodes with a typed edge.
"""

from typing import Any, Dict, List, Optional, Tuple, Union

# Sentinel per distingir "pk no proporcionat" de "pk=None explícit"
_UNSET = object()


def _single_pk(pk: Dict[str, Union[int, str]], version: int = 5) -> Dict[str, Union[int, str]]:
    """Merge multi-field PK into a single-field PK for Neo4j 3.x compatibility.

    Args:
        pk: Primary key dictionary.
        version: Neo4j protocol version (3 or 5).

    Returns:
        A single-field PK dict for version 3, or the original dict for version 5.
    """
    if version == 3 and len(pk) > 1:
        return {"_".join(pk.keys()): "_".join([str(x) for x in pk.values()])}
    return pk


def _new_key(old_key: str, list_keys: set) -> str:
    """Generate a unique key by appending _0, _1, ... until a free name is found.

    Args:
        old_key: The original key to modify.
        list_keys: Set of existing keys to avoid.

    Returns:
        A unique key name.
    """
    n = 0
    while old_key + "_" + str(n) in list_keys:
        n += 1
    return old_key + "_" + str(n)


def _mergePK(
    pk_a: Dict[str, Union[int, str]],
    pk_b: Optional[Dict[str, Union[int, str]]],
    version: int = 5,
) -> Dict[str, Union[int, str]]:
    """Merge two PK dicts into a composite PK.

    If pk_b is None, return pk_a as-is (the child inherits the parent's PK).
    """
    if pk_b is None:
        return pk_a
    pk_b = dict(pk_b)
    k = set(pk_a.keys()).intersection(set(pk_b.keys()))

    for v in k:
        new_key = _new_key(v, set(pk_a.keys()).union(pk_b.keys()))
        pk_b[new_key] = pk_b.pop(v)

    pk = {**pk_a, **pk_b}

    return _single_pk(pk, version)


def _setNodePK(value: Dict[str, Any]) -> Dict[str, Any]:
    """Extract main_label and pk from a dict without mutating the original.

    Args:
        value: dict containing 'main_label' and 'pk' keys.

    Returns:
        A new dict with 'main_label' and 'pk'. Returns empty values
        if the keys are missing.
    """
    main_label = value.get("main_label")
    pk = value.get("pk")
    return {"main_label": main_label, "pk": pk}


class Node:
    """A graph node with a primary key, labels, and optional parent.

    Nodes are the fundamental building blocks of the DRM graph.  Each node
    has a ``main_label`` (the primary Cypher label), optional ``alternative_labels``,
    and a ``pk`` (primary key) that uniquely identifies it within its label.

    When a ``parent`` is provided the node becomes a **WeakNode**: its primary
    key is merged with the parent's key to form a composite key, and a typed
    edge is created when the node is inserted into a graph store.

    By default a node **must** have a ``pk`` or a ``neo4j_id`` (or both).
    If the caller passes ``pk=None`` explicitly, the node is created with
    ``_primary_key = None`` and the backend is expected to assign a real
    ID later (e.g. Neo4j generates an internal node ID, which is then
    stored as ``_primary_key``).  If the backend never assigns one,
    ``_primary_key`` remains ``None``.

    Args:
        pk: Primary key — an int is converted to ``{"id": pk}``, a dict is used
            as-is.  Must be provided (or explicitly ``None``) unless
            ``neo4j_id`` is given.
        main_label: The primary label used in Cypher queries.
        alternative_labels: Additional labels attached to the node.
        version: Neo4j protocol version (default 5).
        neo4j_id: Internal Neo4j node ID (used when reconstructing a node
            from a database result).
        **kwargs: Arbitrary additional attributes stored on the node.
            Special kwargs: ``parent`` (Node), ``parent_relation`` (str),
            ``is_weak`` (bool), ``_propagate`` (bool), ``dependencies``.

    Raises:
        ValueError: If ``pk`` is not provided at all (not even as ``None``)
            and ``neo4j_id`` is also absent.
        TypeError: If ``pk`` is neither an ``int`` nor a ``dict`` (when
            ``pk`` is provided but not ``None``).
    """

    def __init__(
        self,
        pk: Union[Dict[str, Union[int, str]], None, object] = _UNSET,
        main_label: str = "",
        alternative_labels: Optional[Union[str, List[str]]] = None,
        version: int = 5,
        neo4j_id: Optional[int] = None,
        **kwargs: Any,
    ):
        # Definim les propietats
        self._neo4j_id = neo4j_id
        self._version = version

        # Distingim entre "pk no passat" i "pk=None explícit"
        if pk is _UNSET:
            if neo4j_id is not None:
                self._primary_key = {"id": neo4j_id}
            else:
                raise ValueError(
                    "Node must have either a primary key (pk) or a neo4j_id. "
                    "A node without either cannot be inserted or referenced."
                )
        elif pk is None:
            # pk=None explícit: si hi ha neo4j_id, el fem servir com a PK;
            # si no, el backend generarà un ID després (pk queda None).
            if neo4j_id is not None:
                self._primary_key = {"id": neo4j_id}
            else:
                self._primary_key = None
        elif isinstance(pk, int):
            self._primary_key = {"id": pk}
        elif isinstance(pk, Dict):
            self._primary_key = _single_pk(pk, version)
        else:
            raise TypeError(
                f"pk must be an int, dict, or None, got {type(pk).__name__}"
            )

        self._main_label = main_label  # main_label.lower().capitalize()

        if isinstance(alternative_labels, str):
            self._label = [
                alternative_labels
            ]  # [alternative_labels.lower().capitalize()]
        else:
            self._label = (
                alternative_labels
                if alternative_labels is None
                else [x for x in alternative_labels]  # x.lower().capitalize()
            )

        self._is_weak = kwargs.pop("is_weak", False)
        self._propagate = kwargs.pop("_propagate", False)
        parent_relation = kwargs.pop("parent_relation", None)
        self._parent = kwargs.pop("parent", None)
        if self._parent is not None:
            assert isinstance(self._parent, Node), "parent must be a Node"
            self._is_weak = True
            if self._parent._primary_key is None:
                raise ValueError(
                    "WeakNode parent must have a primary key. "
                    "Transient nodes (no pk) cannot be parents."
                )
            self._primary_key = _mergePK(self._parent._primary_key, self._primary_key)
            self._parent_relation = (
                parent_relation if parent_relation is not None else "HAS"
            )

        dependencies = kwargs.pop("dependencies", False)
        if dependencies:
            self._dependencies = dependencies
        else:
            self._dependencies = None

        if kwargs is not None:
            for k in kwargs:
                self.__setattr__(k, kwargs[k])

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __repr__(self):
        main_label = ":" + self._main_label if len(self._main_label) > 0 else ""
        id = " <Id>: " + str(self._neo4j_id) if self._neo4j_id is not None else ""
        mess = "(a" + main_label + id + ")"
        pk_repr = self._primary_key.__repr__() if self._primary_key is not None else "None"
        mess += """, pk:""" + pk_repr
        mess += (
            (", attributes:" + self.attributes[1].__repr__())
            if len(self.attributes[1]) > 0
            else ""
        )
        return mess

    def __getitem__(self, key: str) -> Any:
        def get_pk():
            if hasattr(self,'_primary_key'):
                return self._primary_key
            if hasattr(self,'_neo4j_id'):
                return { 'neo4j_id': self._neo4j_id}

            return 0

        if key == "pk":
            return {"main_label": self._main_label, "pk": get_pk() }

        if key == "main_label":
            return self._main_label

        if key == "labels":
            if self._label is None:
                return [self._main_label]
            else:
                return [self._main_label] + self._label

        if key == "pk_attributes":
            return get_pk()

        attr = self.__dict__.copy()
        if key == "attributes":
            attr.pop("_label", None)
            attr.pop("_main_label")
            attr.pop("_neo4j_id", None)
            pk = attr.pop("_primary_key", None)

            return pk, attr

        if "_" + key in attr:
            return attr["_" + key]
        elif key in attr:
            return attr[key]
        else:
            raise Exception(key + " is not a node attribute")

    def __setitem__(self, key, value):
        if key == "pk":
            aux = _setNodePK(value)
            self._main_label, self._primary_key = aux["main_label"], aux["pk"]
            return

        if key == "main_label":
            self._main_label = value
            return

        if key == "labels":
            self._label = value
            return

        if key == "pk_attributes":
            if isinstance(value, dict):
                self._primary_key = value
            else:
                raise Exception("dictionary expected")

            return

        self.__setattr__(key, value)

    def __delitem__(self, key: str) -> None:
        del self.__dict__[key]

    def __contains__(self, key: str) -> bool:
        return key in self.__dict__ or "_" + key in self.__dict__

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access: return the value for *key*, or *default* if missing.

        This method provides a compatibility shim so that code written
        with ``node.get("key", default)`` works on Node objects even
        though they are not full ``dict`` subclasses.

        Args:
            key: The attribute key to retrieve.
            default: Value returned when the key is not found.

        Returns:
            The attribute value, or *default* if the key is absent.
        """
        try:
            return self[key]
        except Exception:
            return default

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def version(self) -> int:
        return self._version

    @version.setter
    def version(self, value: int) -> None:
        self._version = value
        if value == 3 and self._primary_key is not None:
            self._primary_key = _single_pk(self._primary_key, value)

    @property
    def attributes(self) -> Tuple[Optional[Dict[str, Union[int, str]]], Dict[str, Any]]:
        """Return (pk, attributes) tuple for this node.

        Returns:
            A tuple of (primary_key_dict_or_None, attributes_dict).
        """
        at = self.__dict__.copy()
        pk = at.pop("_primary_key", None)
        for x in list(at.keys()):
            if x[0] == "_":
                at.pop(x, None)
        return pk, at

    @property
    def labels(self) -> List[str]:
        """Return the full list of labels (main + alternative)."""
        if self._label is None:
            return [self._main_label]
        return [self._main_label] + self._label

    @property
    def main_label(self) -> str:
        """Return the primary Cypher label."""
        return self._main_label

    @property
    def neo4j_id(self) -> Optional[int]:
        """Return the internal Neo4j node id, if set."""
        return self._neo4j_id

    @neo4j_id.setter
    def neo4j_id(self, value: Optional[int]) -> None:
        self._neo4j_id = value

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def keys(self) -> List[str]:
        """Return all public attribute names."""
        return [k for k in self.__dict__.keys() if not k.startswith("_")]


# Class Relation denotes the relation of two nodes in the graph database
class Relation:
    """A typed edge connecting two nodes in the graph.

    Relations store the primary keys of their source and destination nodes
    and can carry arbitrary edge properties.

    Args:
        src: Source node. Its ``pk`` is extracted and stored.
        dst: Destination node. Its ``pk`` is extracted and stored.
        type: Relation type (e.g. "HAS_NOM", "CONNECTS"). Stored uppercase.
        **kwargs: Edge properties stored as attributes.
    """

    def __init__(self, src: Node, dst: Node, rel_type: str, **kwargs: Any) -> None:
        """Initialize a Relation between two nodes.

        Args:
            src: Source node. Its ``pk`` is extracted and stored.
            dst: Destination node. Its ``pk`` is extracted and stored.
            rel_type: Relation type (e.g. "HAS_NOM", "CONNECTS"). Stored uppercase.
            **kwargs: Edge properties stored as attributes.
        """
        self._type = rel_type.upper()
        self._src = src["pk"]
        self._dst = dst["pk"]

        if kwargs is not None:
            for k in kwargs:
                self.__setattr__(k, kwargs[k])

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            "src:" + str(self._src) + ", dst:" + str(self._dst)
            + " type:" + self._type
        )

    def __getitem__(self, key: str) -> Any:
        if key == "src":
            return {"main_label": self._src["main_label"], "pk": self._src["pk"]}

        if key == "dst":
            return {"main_label": self._dst["main_label"], "pk": self._dst["pk"]}

        if key == "type":
            return self._type

        attr = self.__dict__.copy()
        if key == "attributes":
            # remove private attributes
            attr.pop("_src", None)
            attr.pop("_dst", None)
            attr.pop("_type", None)

            if attr == {}:
                return None
            else:
                return attr

        if key in attr:
            return attr[key]
        raise KeyError(key + " is not a relation attribute")

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "src":
            self._src = _setNodePK(value)
            return

        if key == "dst":
            self._dst = _setNodePK(value)
            return

        if key == "type":
            self._type = value
            return

        self.__setattr__(key, value)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def src(self) -> Tuple[str, Optional[Dict[str, Union[int, str]]]]:
        """Return (main_label, pk) of the source node."""
        return self._src["main_label"], self._src["pk"]

    @src.setter
    def src(self, value: Dict[str, Any]) -> None:
        self._src = _setNodePK(value)

    @property
    def dst(self) -> Tuple[str, Optional[Dict[str, Union[int, str]]]]:
        """Return (main_label, pk) of the destination node."""
        return self._dst["main_label"], self._dst["pk"]

    @dst.setter
    def dst(self, value: Dict[str, Any]) -> None:
        self._dst = _setNodePK(value)

    @property
    def type(self) -> str:
        """Return the relation type (uppercase)."""
        return self._type

    @property
    def attributes(self) -> Optional[Dict[str, Any]]:
        """Return edge attributes, or None if empty."""
        attr = {
            k: v for k, v in self.__dict__.items()
            if not k.startswith("_")
        }
        return attr if attr else None


class WeakNode(Node):
    """A node whose identity is tied to its parent node.

    WeakNodes form a parent-child hierarchy where the child's primary
    key is **merged** with the parent's key to produce a composite key.
    This models document structures such as *Document → Section → Page*
    where a child cannot exist without its parent.

    When a WeakNode is inserted into a graph store, a typed edge
    (``WeakRelation``) is automatically created linking the parent to
    the child.  This edge carries the ``_propagate=TRUE`` flag, which
    triggers cascade delete: deleting the parent automatically deletes
    all descendants in the hierarchy.

    Args:
        parent: The parent ``Node``. Must not be None.
        **kwargs: Forwarded to :class:`Node.__init__`.  Common kwargs
            include ``pk`` (child's primary key), ``main_label``,
            ``alternative_labels``, ``parent_relation`` (default
            ``"HAS"``), and ``_propagate``.

    Raises:
        AssertionError: If ``parent`` is not a ``Node`` instance.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a WeakNode tied to a parent node.

        Args:
            parent: The parent ``Node``. Required — must be passed as a
                kwarg.
            **kwargs: Passed to :class:`Node.__init__` (``pk``,
                ``main_label``, ``alternative_labels``,
                ``parent_relation``, ``_propagate``, etc.).

        Raises:
            AssertionError: If ``parent`` is not provided or is not a
                ``Node`` instance.
        """
        parent = kwargs.pop("parent", None)
        if parent is None:
            raise ValueError(
                "WeakNode requires a 'parent' argument. "
                "Pass parent=<Node> to establish the parent-child relationship."
            )
        assert isinstance(parent, Node), "parent must be a Node"
        kwargs.pop("is_weak", None)
        super().__init__(is_weak=True, parent=parent, **kwargs)


class WeakRelation(Relation):
    """A typed edge connecting a parent node to its child (WeakNode).

    WeakRelations are automatically created when a WeakNode is inserted.
    They carry the ``_propagate=TRUE`` flag, which signals to the graph
    store that deleting the parent should cascade to the child node.

    Args:
        src: Source (parent) node.
        dst: Destination (child / WeakNode).
        rel_type: Relation type (e.g. ``"HAS_PAGE"``, ``"CONTAINS"``).
        **kwargs: Edge properties.  The ``propagate`` kwarg controls
            whether the ``_propagate`` flag is set (default ``True``).

    Attributes:
        _propagate: Always ``True`` by default.  Indicates that deleting
            the source node should cascade delete to the destination node.
    """

    def __init__(
        self, src: Node, dst: Node, rel_type: str, **kwargs: Any
    ) -> None:
        """Initialize a WeakRelation with cascade propagation.

        Args:
            src: Source (parent) node.
            dst: Destination (child) node.
            rel_type: Relation type.
            propagate: If True (default), the edge carries the
                ``_propagate=TRUE`` flag for cascade delete.
        """
        super().__init__(
            src, dst, rel_type, _propagate=kwargs.pop("propagate", True), **kwargs
        )
