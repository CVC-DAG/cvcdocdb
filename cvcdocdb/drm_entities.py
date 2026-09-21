"""Semantic entity definitions for the DRM graph model.

rThis module defines domain-specific node types such as ``Individu``, ``Lloc``
and ``DocumentCultural``. These classes build on :mod:`cvcdocdb.base` to provide
validation, default labels, and automatic relationship materialisation.

Conceptually, the module is organised around two families of entities:

- root entities, which inherit from :class:`~cvcdocdb.base.Node`
- hierarchical child entities, which inherit from :class:`~cvcdocdb.base.WeakNode`

The concrete classes model people, places, cultural documents, layouts and
other semantic concepts used throughout the tutorials and examples.
"""

from typing import Any, Dict, Iterable, Tuple

from .base import Node, Relation, WeakNode


def assert_on_properties(
    args: Dict,
    properties: Iterable,
    entity: object,
    accepted_types: tuple = (
        str,
        int,
        dict,
        bool,
    ),
) -> Tuple[bool, str]:
    """Precondition on node insertion: asserts if a specified property isn't an instance.

    Args:
        args: dict of arguments for a certain entity.
        properties: iterable with properties to be asserted.
        entity: entity we are checking out.
        accepted_types: tuple of accepted types as property of object
            type Node or Relation. Non-hashable types are rejected.

    Returns:
        A tuple (success: bool, message: str). On success, message is "OKAY".
        On failure, message describes the missing property.
    """
    for prop in properties:
        if prop not in args or not isinstance(args[prop], accepted_types):
            return (
                False,
                f"AssertionError: Node type {type(entity).__name__} must receive "
                f"{prop}, therefore hasn't been added.",
            )
    return (True, "OKAY")


def _build_alternative_labels(al, suffix: str):
    """Append a suffix label to an alternative_labels value.

    Args:
        al: existing alternative labels (list, str, or None).
        suffix: label to append (e.g. "Individu", "Lloc").

    Returns:
        A list of alternative labels with the suffix appended.
    """
    if al is not None:
        if isinstance(al, list):
            return [*al, suffix]
        if isinstance(al, str):
            return [al, suffix]
    return [suffix]


# =================== Nodes =======================


# ========= Individu (person) =============
class Individu(Node):
    """A person entity with a primary key.

    Base class for all person entities. Automatically appends "Individu"
    to alternative labels. Subclasses can define ``be_value_properties``
    to automatically materialise string attributes as ``Atribut`` nodes.

    Args:
        pk: Primary key.
        alternative_labels: Additional labels (appends "Individu").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties: Tuple[str, ...] = ("pk",)
    be_value_properties: Tuple[str, ...] = ()

    def __init__(
        self, ignore_assertion: bool = False, **kwargs: Any
    ) -> None:
        """Initialize an Individu node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to Node.__init__ (pk, alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )
        pk = kwargs.pop("pk", None)
        al = kwargs.pop("alternative_labels", None)
        if al is not None:
            al = _build_alternative_labels(al, "Individu")
        else:
            al = ["Individu"]

        if precondition or ignore_assertion:
            # be_value_properties (e.g. "nom", "cognom1" on IndividuPadro)
            # are materialised as Atribut dependencies generically by
            # Node.__init__ — see base.py.
            Node.__init__(
                self,
                main_label=self._main_label(),
                pk=pk,
                alternative_labels=al,
                **kwargs,
            )
        else:
            print(message)
            exit()

    @classmethod
    def _main_label(cls) -> str:
        """Return the main Cypher label for this class.

        Override in subclasses.
        """
        return cls.__name__


class IndividuPadro(Individu):
    """A person entity with a name attribute.

    Extends Individu with mandatory ``nom`` property. The ``nom`` and
    ``cognom`` attributes are automatically materialised as ``Valor``
    nodes connected by typed edges.

    Args:
        pk: Primary key.
        nom: Person's first name (creates a Valor node).
        cognom1: First surname (creates a Valor node).
        cognom2: Second surname (creates a Valor node).
        alternative_labels: Additional labels (appends "Individu").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk", "nom")
    be_value_properties = ("nom", "cognom1", "cognom2")

    @classmethod
    def _main_label(cls) -> str:
        return "IndividuPadro"


class IndividuFoto(Individu):
    """A person entity linked to a photograph.

    Extends Individu with no additional value properties.

    Args:
        pk: Primary key.
        alternative_labels: Additional labels (appends "Individu").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk",)
    be_value_properties = ()

    @classmethod
    def _main_label(cls) -> str:
        return "IndividuFoto"


# ========= Lloc (place) =============
class Lloc(Node):
    """A place entity with a primary key.

    Base class for all place entities. Automatically appends "Lloc"
    to alternative labels.

    Args:
        main_label: Primary Cypher label (default depends on subclass).
        pk: Primary key.
        alternative_labels: Additional labels (appends "Lloc").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties: Tuple[str, ...] = ("pk",)

    def __init__(
        self, main_label: str = "", ignore_assertion: bool = False, **kwargs: Any
    ) -> None:
        """Initialize a Lloc node.

        Args:
            main_label: Primary Cypher label.
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to Node.__init__ (pk, alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )
        pk = kwargs.pop("pk", None)
        al = kwargs.pop("alternative_labels", None)
        if al is not None:
            al = _build_alternative_labels(al, "Lloc")
        else:
            al = ["Lloc"]

        if precondition or ignore_assertion:
            Node.__init__(
                self,
                main_label=main_label or self._main_label(),
                pk=pk,
                alternative_labels=al,
                **kwargs,
            )
        else:
            print(message)
            exit()

    @classmethod
    def _main_label(cls) -> str:
        return cls.__name__


class LlocPadro(Lloc):
    """A location entity with a custom label.

    Extends Lloc with default label "LlocPadro".

    Args:
        main_label: Primary Cypher label (default "LlocPadro").
        pk: Primary key.
        alternative_labels: Additional labels (appends "Lloc").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk",)

    @classmethod
    def _main_label(cls) -> str:
        return "LlocPadro"


class LlocFoto(Lloc):
    """A location entity linked to a photograph.

    Extends Lloc with default label "LlocFoto".

    Args:
        main_label: Primary Cypher label (default "LlocFoto").
        pk: Primary key.
        alternative_labels: Additional labels (appends "Lloc").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk",)

    @classmethod
    def _main_label(cls) -> str:
        return "LlocFoto"


# ========= DocumentCultural (abstract) =============
class DocumentCultural(Node):
    """An abstract base class for cultural documents.

    This class should not be instantiated directly. Subclasses
    override ``document_class`` to set the appropriate label.
    Automatically appends "DocumentCultural" to alternative labels.

    Attributes:
        document_class: The Cypher label for this document type.
            Defaults to "abstract_class".
    """

    document_class = "abstract_class"

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a DocumentCultural node.

        Args:
            **kwargs: Passed to Node.__init__ (alternative_labels, etc.).
        """
        al = kwargs.pop("alternative_labels", None)
        if al is not None:
            al = _build_alternative_labels(al, "DocumentCultural")
        else:
            al = ["DocumentCultural"]

        Node.__init__(
            self,
            alternative_labels=al,
            main_label=self.document_class,
            **kwargs,
        )


class Fons(DocumentCultural):
    """A document collection (fonds) entity.

    Extends DocumentCultural with label "Fons". This is the root
    entity under which all other documents are hierarchically organised.

    Args:
        pk: Primary key.
        alternative_labels: Additional labels (appends "DocumentCultural").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk",)

    document_class = "Fons"

    def __init__(self, ignore_assertion: bool = False, **kwargs: Any) -> None:
        """Initialize a Fons node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to Node.__init__ (pk, alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, ("pk",), self
        )
        pk = kwargs.pop("pk", None)

        if precondition or ignore_assertion:
            Node.__init__(
                self,
                main_label="Fons",
                pk=pk,
                alternative_labels="DocumentCultural",
                **kwargs,
            )
        else:
            print(message)
            exit()


class ActaTemporal(DocumentCultural):
    """A temporary act/document entity.

    Extends DocumentCultural with label "ActaTemporal".

    Args:
        pk: Primary key.
        alternative_labels: Additional labels (appends "DocumentCultural").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk",)

    document_class = "ActaTemporal"

    def __init__(self, ignore_assertion: bool = False, **kwargs: Any) -> None:
        """Initialize an ActaTemporal node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to Node.__init__ (pk, alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, ("pk",), self
        )
        pk = kwargs.pop("pk", None)

        if precondition or ignore_assertion:
            Node.__init__(
                self,
                main_label="ActaTemporal",
                pk=pk,
                alternative_labels="DocumentCultural",
                **kwargs,
            )
        else:
            print(message)
            exit()


# ========= Other semantic nodes =============
class EntitatAmbNom(Node):
    """A named entity with a ``nom`` property.

    Extends Node with mandatory ``pk`` and ``nom`` properties.

    Args:
        pk: Primary key.
        nom: Entity name.
        alternative_labels: Additional labels.
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk", "nom")

    def __init__(self, ignore_assertion: bool = False, **kwargs: Any) -> None:
        """Initialize an EntitatAmbNom node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to Node.__init__ (pk, nom, alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )
        pk = kwargs.pop("pk", None)
        al = kwargs.pop("alternative_labels", None)

        if precondition or ignore_assertion:
            Node.__init__(
                self,
                main_label="ner_tag",
                pk=pk,
                alternative_labels=al,
                **kwargs,
            )
        else:
            print(message)
            exit()


class IndividuAgregat(Node):
    """An aggregated person entity with configurable mandatory properties.

    Extends Node with a configurable set of mandatory properties
    derived from a tuple or list of property names.

    Args:
        pk: Primary key (defaults to empty string).
        main_label: Primary Cypher label (defaults to "PersonaAgregat").
        alternative_labels: Additional labels (appends "Individu").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties: Tuple[str, ...] = ("pk",)

    def __init__(
        self,
        pk: str = "",
        main_label: str = "PersonaAgregat",
        ignore_assertion: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize an IndividuAgregat node.

        Args:
            pk: Primary key.
            main_label: Primary Cypher label.
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to Node.__init__ (alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )
        al = kwargs.pop("alternative_labels", None)
        if al is not None:
            al = _build_alternative_labels(al, "Individu")
        else:
            al = ["Individu"]

        if precondition or ignore_assertion:
            Node.__init__(
                self,
                main_label=main_label,
                pk=pk,
                alternative_labels=al,
                **kwargs,
            )
        else:
            print(message)
            exit()


class Atribut(Node):
    """A value node wrapping a string attribute.

    Extends Node to represent a string value as a graph node with
    label "Valor" and a ``name`` primary key derived from the value.

    Args:
        value: The string value to wrap.
        **kwargs: Additional node attributes passed to Node.__init__.
    """

    def __init__(self, value: str, **kwargs: Any) -> None:
        """Initialize an Atribut value node.

        Args:
            value: The string value. Stored as ``{"name": value.lower()}`` pk.
            **kwargs: Passed to Node.__init__ (alternative_labels, etc.).
        """
        Node.__init__(
            self,
            pk={"name": value.lower()},
            main_label="Valor",
            **kwargs,
        )


class Esdeventiment(Node):
    """An event entity.

    Extends Node with mandatory ``pk`` property and label "Esdeveniment".

    Args:
        pk: Primary key.
        alternative_labels: Additional labels (appends "Semantic").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes.
    """

    mandatory_properties = ("pk",)

    def __init__(
        self, ignore_assertion: bool = False, **kwargs: Any
    ) -> None:
        """Initialize an Esdeventiment node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to Node.__init__ (pk, alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )

        if precondition or ignore_assertion:
            Node.__init__(
                self,
                main_label="Esdeveniment",
                pk=kwargs.get("pk"),
                alternative_labels="Semantic",
                **kwargs,
            )
        else:
            print(message)
            exit()


# =================== Weak Nodes =======================


# ========= Layout (abstract) =============
class Layout(WeakNode):
    """An abstract base class for layout entities.

    This class should not be instantiated directly. Subclasses
    override ``main_label`` to set the appropriate Cypher label.
    Automatically appends "layout" to alternative labels.

    Args:
        parent: The parent ``Node``.
        main_label: Primary Cypher label.
        pk: Primary key.
        alternative_labels: Additional labels (appends "layout").
        **kwargs: Additional node attributes passed to WeakNode.__init__.
    """

    main_label: str = "Layout"

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a Layout weak node.

        Args:
            **kwargs: Passed to WeakNode.__init__ (main_label, pk,
                parent, parent_relation, alternative_labels, etc.).
        """
        main_label = kwargs.pop("main_label", self.main_label)
        pk = kwargs.pop("pk", None)
        al = kwargs.pop("alternative_labels", None)
        if al is not None:
            al = _build_alternative_labels(al, "layout")
        else:
            al = ["layout"]

        super().__init__(
            main_label=main_label,
            pk=pk,
            alternative_labels=al,
            **kwargs,
        )


class RegioFisica(Layout):
    """A physical region entity with contours.

    Extends Layout with mandatory ``pk`` and ``contours`` properties.

    Args:
        parent: The parent ``Node``.
        main_label: Primary Cypher label.
        pk: Primary key.
        contours: Region contour data.
        alternative_labels: Additional labels (appends "layout").
        **kwargs: Additional node attributes passed to WeakNode.__init__.
    """

    mandatory_properties = ("pk", "contours")
    main_label = "RegioFisica"

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a RegioFisica weak node.

        Args:
            **kwargs: Passed to WeakNode.__init__ (main_label, pk, contours,
                parent, parent_relation, alternative_labels, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )

        if precondition:
            main_label = kwargs.pop("main_label", self.main_label)
            pk = kwargs.pop("pk", None)
            al = kwargs.pop("alternative_labels", None)
            if al is not None:
                al = _build_alternative_labels(al, "layout")
            else:
                al = ["layout"]

            super().__init__(
                main_label=main_label,
                pk=pk,
                alternative_labels=al,
                **kwargs,
            )
        else:
            print(message)
            exit()


class OCRTranscript(Layout):
    """An OCR transcript entity.

    Extends Layout with a ``main_label`` and optional ``pk``.
    Automatically appends "semantic" to alternative labels.

    Args:
        parent: The parent ``Node``.
        main_label: Primary Cypher label.
        pk: Primary key.
        alternative_labels: Additional labels (appends "semantic").
        **kwargs: Additional node attributes passed to WeakNode.__init__.
    """

    main_label = "OCRTranscript"

    def __init__(self, **kwargs: Any) -> None:
        """Initialize an OCRTranscript weak node.

        Args:
            **kwargs: Passed to WeakNode.__init__ (main_label, pk,
                parent, parent_relation, alternative_labels, etc.).
        """
        main_label = kwargs.pop("main_label", self.main_label)
        pk = kwargs.pop("pk", None)
        al = kwargs.pop("alternative_labels", None)
        if al is not None:
            al = _build_alternative_labels(al, "semantic")
        else:
            al = ["semantic"]

        super().__init__(
            main_label=main_label,
            pk=pk,
            alternative_labels=al,
            **kwargs,
        )


# ========= DocumentCultural (abstract, WeakNode) =============
# Note: DocumentCultural as a WeakNode base for child documents
class _DocumentCulturalBase(WeakNode):
    """Abstract base for child document entities (WeakNode).

    This class should not be instantiated directly. Subclasses
    override ``document_class`` to set the appropriate label.
    Automatically appends "document" to alternative labels.

    Args:
        parent: The parent ``Node``.
        pk: Primary key.
        alternative_labels: Additional labels (appends "document").
        **kwargs: Additional node attributes passed to WeakNode.__init__.
    """

    document_class: str = "abstract_class"

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a child document weak node.

        Args:
            **kwargs: Passed to WeakNode.__init__ (pk, alternative_labels,
                parent, parent_relation, etc.).
        """
        pk = kwargs.pop("pk", None)
        al = kwargs.pop("alternative_labels", None)
        if al is not None:
            al = _build_alternative_labels(al, "document")
        else:
            al = ["document"]

        super().__init__(
            main_label=self.document_class,
            pk=pk,
            alternative_labels=al,
            **kwargs,
        )


class Padro(_DocumentCulturalBase):
    """A padro document entity with a path property.

    Extends _DocumentCulturalBase with mandatory ``pk`` and ``ruta`` properties.

    Args:
        parent: The parent ``Node``.
        pk: Primary key.
        ruta: Path property.
        alternative_labels: Additional labels (appends "document").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes passed to WeakNode.__init__.
    """

    mandatory_properties = ("pk", "ruta")
    document_class = "Padro"

    def __init__(self, ignore_assertion: bool = False, **kwargs: Any) -> None:
        """Initialize a Padro weak node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to WeakNode.__init__ (pk, ruta, alternative_labels,
                parent, parent_relation, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )
        pk = kwargs.pop("pk", None)

        if precondition or ignore_assertion:
            super().__init__(
                pk=pk,
                **kwargs,
            )
        else:
            print(message)
            exit()


class Fotografia(_DocumentCulturalBase):
    """A photograph entity.

    Extends _DocumentCulturalBase with mandatory ``pk`` property.

    Args:
        parent: The parent ``Node``.
        pk: Primary key.
        alternative_labels: Additional labels (appends "document").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes passed to WeakNode.__init__.
    """

    mandatory_properties = ("pk",)
    document_class = "Fotografia"

    def __init__(self, ignore_assertion: bool = False, **kwargs: Any) -> None:
        """Initialize a Fotografia weak node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to WeakNode.__init__ (pk, alternative_labels,
                parent, parent_relation, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )
        pk = kwargs.pop("pk", None)

        if precondition or ignore_assertion:
            super().__init__(
                pk=pk,
                **kwargs,
            )
        else:
            print(message)
            exit()


class BOE(_DocumentCulturalBase):
    """A Boletín Oficial del Estado (Spanish government gazette) entity.

    Extends _DocumentCulturalBase with mandatory ``pk`` and ``ruta`` properties.

    Args:
        parent: The parent ``Node``.
        pk: Primary key.
        ruta: Path to the document.
        alternative_labels: Additional labels (appends "document").
        ignore_assertion: If True, skip mandatory property checks.
        **kwargs: Additional node attributes passed to WeakNode.__init__.
    """

    mandatory_properties = ("pk", "ruta")
    document_class = "BOE"

    def __init__(self, ignore_assertion: bool = False, **kwargs: Any) -> None:
        """Initialize a BOE weak node.

        Args:
            ignore_assertion: Skip mandatory property checks.
            **kwargs: Passed to WeakNode.__init__ (pk, ruta, alternative_labels,
                parent, parent_relation, etc.).
        """
        precondition, message = assert_on_properties(
            kwargs, self.mandatory_properties, self
        )
        pk = kwargs.pop("pk", None)

        if precondition or ignore_assertion:
            super().__init__(
                pk=pk,
                **kwargs,
            )
        else:
            print(message)
            exit()


NODES = [
    IndividuPadro,
    IndividuFoto,
    Atribut,
    IndividuAgregat,
    Esdeventiment,
    LlocPadro,
    LlocFoto,
    ActaTemporal,
    Fons,
    Padro,
    Fotografia,
]

EDGES = []
