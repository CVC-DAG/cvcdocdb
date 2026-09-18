"""Auto-generated entity classes from schema."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from cvcdocdb.base import Node, WeakNode, Relation, WeakRelation


class Character(Node):
    """Auto-generated entity class.."""

    def __init__(self, pk: Dict[str, Any], **kwargs: Any) -> None:
        """Initialize a Character node.

        Args:
            pk: Primary key dict with fields: name, character_id.
            **kwargs: Additional properties and attributes.
        """
        super().__init__(pk=pk, main_label="Character", **kwargs)
        self.character_id = kwargs.get("character_id", 'integer')
        self.name = kwargs.get("name", 'string')
        self.source = kwargs.get("source", 'string')
        self.title = kwargs.get("title", 'string')
class House(Node):
    """Auto-generated entity class.."""

    def __init__(self, pk: Dict[str, Any], **kwargs: Any) -> None:
        """Initialize a House node.

        Args:
            pk: Primary key dict with fields: house_name, name.
            **kwargs: Additional properties and attributes.
        """
        super().__init__(pk=pk, main_label="House", **kwargs)
        self.house_name = kwargs.get("house_name", 'string')
        self.name = kwargs.get("name", 'string')
class Place(Node):
    """Auto-generated entity class.."""

    def __init__(self, pk: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        """Initialize a Place node.

        Args:
            pk: Optional primary key dict. When not provided, the backend assigns an ID.
            **kwargs: Additional properties and attributes.
        """
        super().__init__(pk=pk, main_label="Place", **kwargs)
class TestNode(Node):
    """Auto-generated entity class.."""

    def __init__(self, pk: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        """Initialize a TestNode node.

        Args:
            pk: Optional primary key dict. When not provided, the backend assigns an ID.
            **kwargs: Additional properties and attributes.
        """
        super().__init__(pk=pk, main_label="TestNode", **kwargs)
class MemberOf(Relation):
    """Auto-generated relation class."""

    def __init__(self, src: Node, dst: Node, **kwargs: Any) -> None:
        """Initialize a MemberOf relation.

        Args:
            src: Source node.
            dst: Destination node.
            **kwargs: Edge properties.
        """
        super().__init__(src=src, dst=dst, rel_type="MEMBER_OF", **kwargs)
