"""Read-only Cypher engine for :class:`~cvcdocdb.networkx_graph.NetworkXGraph`.

``NetworkXGraph.query(cypher)`` routes every read-only query here (write
queries keep using the older engine in ``networkx_graph``). Instead of
matching the query text with regular expressions, it is tokenized, parsed
into a small AST and evaluated against the graph, following Neo4j's
semantics for the supported subset:

- Clauses: ``MATCH``, ``OPTIONAL MATCH``, ``WHERE``, ``WITH``, ``RETURN``
  (``DISTINCT``, ``*``), ``ORDER BY`` (``ASC``/``DESC``), ``SKIP``, ``LIMIT``.
- Patterns: ``(v:Label1:Label2 {key: expr})``, anonymous nodes, chains of
  relationships ``-[r:T1|T2 {key: expr}]->``, ``<-[...]-``, ``-[...]-``,
  several comma-separated patterns. Main and alternative labels both match.
  A relationship is not matched twice within one ``MATCH`` (as in Neo4j).
- Expressions: literals (numbers, strings, booleans, ``null``, lists, maps),
  ``$params`` (bound as values, never substituted into the text), property
  access, ``AND``/``OR``/``XOR``/``NOT`` with three-valued ``null`` logic,
  ``=``, ``<>``, ``<``, ``<=``, ``>``, ``>=``, ``IS [NOT] NULL``, ``IN``,
  ``STARTS WITH``, ``ENDS WITH``, ``CONTAINS``, ``=~``, label predicates
  (``n:Label``), arithmetic, and the functions in :data:`_SCALAR_FUNCTIONS`.
- Aggregations: ``count`` (incl. ``count(*)`` and ``DISTINCT``), ``sum``,
  ``avg``, ``min``, ``max``, ``collect``, with implicit grouping by the
  non-aggregated items.

Anything outside this subset (variable-length paths, path variables,
``UNWIND``, ``UNION``, ``CASE``, subqueries, unknown functions...) raises
:class:`NxCypherError` (a ``ValueError``) instead of returning wrong results.

Returned values have the same shape as before: nodes as
``{"labels": [...], "properties": {...}}`` and relationships as
``{"type", "start_node", "end_node", "properties"}``.
"""

import functools
import math
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

__all__ = ["NxCypherError", "execute_read", "is_write_query"]


class NxCypherError(ValueError):
    """Invalid or unsupported Cypher for the NetworkX engine."""


# ----------------------------------------------------------------------
# Tokenizer
# ----------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<float>\d+\.\d+(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<int>\d+)
  | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<quoted>`(?:[^`]|``)*`)
  | (?P<param>\$(?:\w+|`[^`]*`))
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><>|!=|<=|>=|=~|\.\.|[()\[\]{},:.|*+\-/%^=<>;])
    """,
    re.VERBOSE | re.DOTALL,
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "\\": "\\", "'": "'", '"': '"'}


class _Tok:
    __slots__ = ("kind", "value", "start", "end")

    def __init__(self, kind: str, value: Any, start: int, end: int) -> None:
        self.kind, self.value, self.start, self.end = kind, value, start, end

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_Tok({self.kind}, {self.value!r})"


def _unescape(body: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), body)


def _tokenize(text: str) -> List[_Tok]:
    tokens: List[_Tok] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise NxCypherError(f"Unexpected character {text[pos]!r} at position {pos}")
        kind = m.lastgroup
        raw = m.group()
        if kind == "ws":
            pass
        elif kind == "float":
            tokens.append(_Tok("num", float(raw), m.start(), m.end()))
        elif kind == "int":
            tokens.append(_Tok("num", int(raw), m.start(), m.end()))
        elif kind == "string":
            tokens.append(_Tok("str", _unescape(raw[1:-1]), m.start(), m.end()))
        elif kind == "quoted":
            tokens.append(_Tok("name", raw[1:-1].replace("``", "`"), m.start(), m.end()))
        elif kind == "param":
            tokens.append(_Tok("param", raw[1:].strip("`"), m.start(), m.end()))
        elif kind == "ident":
            tokens.append(_Tok("ident", raw, m.start(), m.end()))
        else:
            tokens.append(_Tok("op", raw, m.start(), m.end()))
        pos = m.end()
    tokens.append(_Tok("eof", None, len(text), len(text)))
    return tokens


_WRITE_KEYWORDS = {"CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE"}


def is_write_query(cypher: str) -> bool:
    """Whether *cypher* contains a clause that modifies the graph."""
    try:
        tokens = _tokenize(cypher)
    except NxCypherError:
        # Fall back to a plain text check; the caller decides what to do.
        return bool(re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b", cypher, re.I))
    for i, tok in enumerate(tokens):
        if tok.kind == "ident" and tok.value.upper() in _WRITE_KEYWORDS:
            prev = tokens[i - 1] if i else None
            if prev is None or not (prev.kind == "op" and prev.value in (".", ":")):
                return True
    return False


# ----------------------------------------------------------------------
# AST
# ----------------------------------------------------------------------
#
# Expressions are tuples whose first item is the node kind:
#   ("lit", value) ("param", name) ("var", name) ("prop", expr, key)
#   ("list", [expr]) ("map", [(key, expr)]) ("index", expr, expr)
#   ("not", expr) ("and"|"or"|"xor", a, b) ("neg", expr)
#   ("cmp", op, a, b)  op in = <> < <= > >= =~ IN STARTS ENDS CONTAINS
#   ("isnull", expr, negated) ("haslabels", expr, [labels])
#   ("arith", op, a, b) ("call", name, [args], distinct) ("countstar",)


class _NodePat:
    __slots__ = ("var", "labels", "props")

    def __init__(self, var: str, labels: List[str], props: List[Tuple[str, tuple]]) -> None:
        self.var, self.labels, self.props = var, labels, props


class _RelPat:
    __slots__ = ("var", "types", "props", "direction")

    def __init__(self, var: str, types: List[str], props: List[Tuple[str, tuple]], direction: str) -> None:
        self.var, self.types, self.props, self.direction = var, types, props, direction


class _Match:
    def __init__(self, patterns: List[Tuple[_NodePat, List[Tuple[_RelPat, _NodePat]]]],
                 where: Optional[tuple], optional: bool) -> None:
        self.patterns, self.where, self.optional = patterns, where, optional


class _Projection:
    """RETURN or WITH."""

    def __init__(self, kind: str, distinct: bool, star: bool,
                 items: List[Tuple[tuple, str, str]],
                 order: List[Tuple[tuple, str, bool]], skip: Optional[tuple],
                 limit: Optional[tuple], where: Optional[tuple]) -> None:
        self.kind, self.distinct, self.star, self.items = kind, distinct, star, items
        self.order, self.skip, self.limit, self.where = order, skip, limit, where


_AGGREGATES = {"count", "sum", "avg", "min", "max", "collect"}
_CLAUSE_STARTS = {"MATCH", "OPTIONAL", "WITH", "RETURN", "WHERE", "ORDER", "SKIP", "LIMIT"}
_UNSUPPORTED_CLAUSES = {
    "UNWIND", "UNION", "CALL", "FOREACH", "LOAD", "USE", "CASE", "EXISTS",
    "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE",
}


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0
        self._anon = 0

    # -- token helpers -------------------------------------------------

    @property
    def tok(self) -> _Tok:
        return self.toks[self.i]

    def peek(self, offset: int = 1) -> _Tok:
        return self.toks[min(self.i + offset, len(self.toks) - 1)]

    def advance(self) -> _Tok:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def is_kw(self, *words: str, tok: Optional[_Tok] = None) -> bool:
        tok = tok or self.tok
        return tok.kind == "ident" and tok.value.upper() in words

    def is_op(self, *ops: str, tok: Optional[_Tok] = None) -> bool:
        tok = tok or self.tok
        return tok.kind == "op" and tok.value in ops

    def accept_kw(self, *words: str) -> bool:
        if self.is_kw(*words):
            self.i += 1
            return True
        return False

    def accept_op(self, *ops: str) -> bool:
        if self.is_op(*ops):
            self.i += 1
            return True
        return False

    def expect_kw(self, word: str) -> None:
        if not self.accept_kw(word):
            self.error(f"expected {word}")

    def expect_op(self, op: str) -> None:
        if not self.accept_op(op):
            self.error(f"expected {op!r}")

    def error(self, msg: str) -> None:
        tok = self.tok
        where = "end of query" if tok.kind == "eof" else repr(self.text[tok.start:tok.end])
        raise NxCypherError(f"Cypher: {msg} at {where} (position {tok.start})")

    def name(self) -> str:
        if self.tok.kind in ("ident", "name"):
            return self.advance().value
        self.error("expected a name")
        return ""  # unreachable

    def anon(self) -> str:
        self._anon += 1
        return f"  anon{self._anon}"

    # -- query ---------------------------------------------------------

    def parse(self) -> List[Any]:
        clauses: List[Any] = []
        while self.tok.kind != "eof":
            if self.accept_op(";"):
                if self.tok.kind != "eof":
                    self.error("multiple statements are not supported")
                break
            if self.is_kw("MATCH") or (self.is_kw("OPTIONAL") and self.is_kw("MATCH", tok=self.peek())):
                clauses.append(self.match_clause())
            elif self.is_kw("WITH", "RETURN"):
                clauses.append(self.projection())
                if clauses[-1].kind == "RETURN" and self.tok.kind != "eof" and not self.is_op(";"):
                    self.error("unsupported clause after RETURN")
            elif self.is_kw(*_UNSUPPORTED_CLAUSES):
                self.error(f"unsupported clause {self.tok.value.upper()}")
            else:
                self.error("unexpected token")
        if not clauses:
            raise NxCypherError("Cypher: empty query")
        if not isinstance(clauses[-1], _Projection) or clauses[-1].kind != "RETURN":
            raise NxCypherError("Cypher: a read query must end with RETURN")
        return clauses

    def match_clause(self) -> _Match:
        optional = self.accept_kw("OPTIONAL")
        self.expect_kw("MATCH")
        patterns = [self.pattern()]
        while self.accept_op(","):
            patterns.append(self.pattern())
        where = self.expr() if self.accept_kw("WHERE") else None
        return _Match(patterns, where, optional)

    def pattern(self) -> Tuple[_NodePat, List[Tuple[_RelPat, _NodePat]]]:
        if self.tok.kind in ("ident", "name") and self.is_op("=", tok=self.peek()):
            self.error("path variables are not supported")
        if self.is_kw("shortestPath", "allShortestPaths"):
            self.error("shortest paths are not supported")
        first = self.node_pattern()
        chain: List[Tuple[_RelPat, _NodePat]] = []
        while self.is_op("-", "<"):
            rel = self.rel_pattern()
            chain.append((rel, self.node_pattern()))
        return first, chain

    def node_pattern(self) -> _NodePat:
        self.expect_op("(")
        var = self.name() if self.tok.kind in ("ident", "name") else self.anon()
        labels: List[str] = []
        while self.accept_op(":"):
            labels.append(self.name())
        props = self.map_literal() if self.is_op("{") else []
        if self.is_kw("WHERE"):
            self.error("inline WHERE in patterns is not supported")
        self.expect_op(")")
        return _NodePat(var, labels, props)

    def rel_pattern(self) -> _RelPat:
        left = self.accept_op("<")
        self.expect_op("-")
        var, types, props = self.anon(), [], []
        if self.accept_op("["):
            if self.tok.kind in ("ident", "name"):
                var = self.name()
            if self.accept_op(":"):
                types.append(self.name())
                while self.accept_op("|"):
                    self.accept_op(":")
                    types.append(self.name())
            if self.is_op("*"):
                self.error("variable-length relationships are not supported")
            if self.is_op("{"):
                props = self.map_literal()
            self.expect_op("]")
        self.expect_op("-")
        right = self.accept_op(">")
        direction = "both" if left == right else ("in" if left else "out")
        return _RelPat(var, types, props, direction)

    def map_literal(self) -> List[Tuple[str, tuple]]:
        self.expect_op("{")
        entries: List[Tuple[str, tuple]] = []
        if not self.is_op("}"):
            while True:
                key = self.name()
                self.expect_op(":")
                entries.append((key, self.expr()))
                if not self.accept_op(","):
                    break
        self.expect_op("}")
        return entries

    def projection(self) -> _Projection:
        kind = self.advance().value.upper()
        distinct = self.accept_kw("DISTINCT")
        star = False
        items: List[Tuple[tuple, str, str]] = []
        if self.accept_op("*"):
            star = True
            if not self.accept_op(","):
                return self._projection_tail(kind, distinct, star, items)
        while True:
            start = self.tok.start
            expr = self.expr()
            text = self.text[start:self.toks[self.i - 1].end].strip()
            if self.accept_kw("AS"):
                alias = self.name()
            elif expr[0] == "var":
                alias = expr[1]
            else:
                alias = text
            items.append((expr, alias, text))
            if not self.accept_op(","):
                break
        return self._projection_tail(kind, distinct, star, items)

    def _projection_tail(self, kind: str, distinct: bool, star: bool,
                         items: List[Tuple[tuple, str, str]]) -> _Projection:
        order: List[Tuple[tuple, str, bool]] = []
        if self.accept_kw("ORDER"):
            self.expect_kw("BY")
            while True:
                start = self.tok.start
                expr = self.expr()
                text = self.text[start:self.toks[self.i - 1].end].strip()
                desc = False
                if self.accept_kw("DESC", "DESCENDING"):
                    desc = True
                else:
                    self.accept_kw("ASC", "ASCENDING")
                order.append((expr, text, desc))
                if not self.accept_op(","):
                    break
        skip = self.expr() if self.accept_kw("SKIP", "OFFSET") else None
        limit = self.expr() if self.accept_kw("LIMIT") else None
        where = None
        if kind == "WITH" and self.accept_kw("WHERE"):
            where = self.expr()
        return _Projection(kind, distinct, star, items, order, skip, limit, where)

    # -- expressions ---------------------------------------------------

    def expr(self) -> tuple:
        return self.or_expr()

    def or_expr(self) -> tuple:
        node = self.xor_expr()
        while self.accept_kw("OR"):
            node = ("or", node, self.xor_expr())
        return node

    def xor_expr(self) -> tuple:
        node = self.and_expr()
        while self.accept_kw("XOR"):
            node = ("xor", node, self.and_expr())
        return node

    def and_expr(self) -> tuple:
        node = self.not_expr()
        while self.accept_kw("AND"):
            node = ("and", node, self.not_expr())
        return node

    def not_expr(self) -> tuple:
        if self.accept_kw("NOT"):
            return ("not", self.not_expr())
        return self.comparison()

    def comparison(self) -> tuple:
        node = self.additive()
        while True:
            if self.is_op("=", "<>", "!=", "<", "<=", ">", ">=", "=~"):
                op = self.advance().value
                node = ("cmp", "<>" if op == "!=" else op, node, self.additive())
            elif self.accept_kw("IN"):
                node = ("cmp", "IN", node, self.additive())
            elif self.is_kw("STARTS", "ENDS"):
                op = self.advance().value.upper()
                self.expect_kw("WITH")
                node = ("cmp", op, node, self.additive())
            elif self.accept_kw("CONTAINS"):
                node = ("cmp", "CONTAINS", node, self.additive())
            elif self.is_kw("IS"):
                self.advance()
                negated = self.accept_kw("NOT")
                self.expect_kw("NULL")
                node = ("isnull", node, negated)
            else:
                return node

    def additive(self) -> tuple:
        node = self.multiplicative()
        while self.is_op("+", "-"):
            op = self.advance().value
            node = ("arith", op, node, self.multiplicative())
        return node

    def multiplicative(self) -> tuple:
        node = self.power()
        while self.is_op("*", "/", "%"):
            op = self.advance().value
            node = ("arith", op, node, self.power())
        return node

    def power(self) -> tuple:
        node = self.unary()
        while self.accept_op("^"):
            node = ("arith", "^", node, self.unary())
        return node

    def unary(self) -> tuple:
        if self.accept_op("-"):
            return ("neg", self.unary())
        if self.accept_op("+"):
            return self.unary()
        return self.postfix()

    def postfix(self) -> tuple:
        node = self.atom()
        while True:
            if self.accept_op("."):
                node = ("prop", node, self.name())
            elif self.is_op("["):
                self.advance()
                if self.is_op(".."):
                    self.error("list slices are not supported")
                index = self.expr()
                if self.is_op(".."):
                    self.error("list slices are not supported")
                self.expect_op("]")
                node = ("index", node, index)
            elif self.is_op(":") and node[0] == "var":
                labels = []
                while self.accept_op(":"):
                    labels.append(self.name())
                node = ("haslabels", node, labels)
            else:
                return node

    def atom(self) -> tuple:
        tok = self.tok
        if tok.kind == "num" or tok.kind == "str":
            self.advance()
            return ("lit", tok.value)
        if tok.kind == "param":
            self.advance()
            return ("param", tok.value)
        if self.accept_op("("):
            node = self.expr()
            if not self.accept_op(")"):
                self.error("expected ')' (pattern predicates are not supported)")
            return node
        if self.accept_op("["):
            items = []
            if not self.is_op("]"):
                while True:
                    items.append(self.expr())
                    if not self.accept_op(","):
                        break
            if self.is_kw("WHERE") or self.is_op("|"):
                self.error("list comprehensions are not supported")
            self.expect_op("]")
            return ("list", items)
        if self.is_op("{"):
            return ("map", self.map_literal())
        if tok.kind == "ident":
            upper = tok.value.upper()
            if upper in ("TRUE", "FALSE"):
                self.advance()
                return ("lit", upper == "TRUE")
            if upper == "NULL":
                self.advance()
                return ("lit", None)
            if upper in ("CASE", "EXISTS", "COUNT") and self.is_op("{", tok=self.peek()):
                self.error("subqueries are not supported")
            if upper == "CASE":
                self.error("CASE expressions are not supported")
            if self.is_op("(", tok=self.peek()) or (
                self.is_op(".", tok=self.peek()) and self._is_namespaced_call()
            ):
                return self.call()
        if tok.kind in ("ident", "name"):
            self.advance()
            return ("var", tok.value)
        self.error("unexpected token")
        return ("lit", None)  # unreachable

    def _is_namespaced_call(self) -> bool:
        j = self.i
        while self.toks[j].kind == "ident" and self.is_op(".", tok=self.toks[j + 1]):
            j += 2
        return self.toks[j].kind == "ident" and self.is_op("(", tok=self.toks[j + 1])

    def call(self) -> tuple:
        name = self.advance().value
        while self.accept_op("."):
            name += "." + self.name()
        self.expect_op("(")
        fname = name.lower()
        if fname == "count" and self.accept_op("*"):
            self.expect_op(")")
            return ("countstar",)
        distinct = self.accept_kw("DISTINCT")
        args = []
        if not self.is_op(")"):
            while True:
                args.append(self.expr())
                if not self.accept_op(","):
                    break
        self.expect_op(")")
        if fname not in _AGGREGATES and fname not in _SCALAR_FUNCTIONS:
            raise NxCypherError(f"Cypher: unsupported function {name}()")
        if distinct and fname not in _AGGREGATES:
            raise NxCypherError(f"Cypher: DISTINCT is only valid in aggregations, not {name}()")
        return ("call", fname, args, distinct)


# ----------------------------------------------------------------------
# Values
# ----------------------------------------------------------------------


class _NodeRef:
    __slots__ = ("id",)

    def __init__(self, nid: Any) -> None:
        self.id = nid

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, _NodeRef) and other.id == self.id

    def __hash__(self) -> int:
        return hash(("node", self.id))


class _RelRef:
    __slots__ = ("key",)

    def __init__(self, key: Tuple[Any, Any, str]) -> None:
        self.key = key

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, _RelRef) and other.key == self.key

    def __hash__(self) -> int:
        return hash(("rel", self.key))


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _hashable(v: Any) -> Any:
    if isinstance(v, list):
        return ("__list__", tuple(_hashable(x) for x in v))
    if isinstance(v, dict):
        return ("__map__", tuple(sorted((k, _hashable(x)) for k, x in v.items())))
    if isinstance(v, float) and v.is_integer():
        return int(v)  # 1 and 1.0 group together, as in Neo4j
    return v


def _equals(a: Any, b: Any) -> Optional[bool]:
    if a is None or b is None:
        return None
    if _is_num(a) and _is_num(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        result: Optional[bool] = True
        for x, y in zip(a, b):
            eq = _equals(x, y)
            if eq is False:
                return False
            if eq is None:
                result = None
        return result
    if type(a) is not type(b):
        return False
    return a == b


def _compare(op: str, a: Any, b: Any) -> Optional[bool]:
    if a is None or b is None:
        return None
    comparable = (_is_num(a) and _is_num(b)) or (
        type(a) is type(b) and isinstance(a, (str, bool))
    )
    if not comparable:
        return None
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    return a >= b


def _truth(v: Any) -> Optional[bool]:
    if v is None or isinstance(v, bool):
        return v
    raise NxCypherError(f"Cypher: expected a boolean, got {v!r}")


_TYPE_RANK = {dict: 0, _NodeRef: 1, _RelRef: 2, list: 3, str: 5, bool: 6}


def _order_cmp(a: Any, b: Any) -> int:
    """Total order used by ORDER BY: null is the largest value (last when
    ascending, first when descending), like Neo4j."""
    if a is None or b is None:
        return (a is None) - (b is None)
    ra = 7 if _is_num(a) else _TYPE_RANK.get(type(a), 4)
    rb = 7 if _is_num(b) else _TYPE_RANK.get(type(b), 4)
    if ra != rb:
        return -1 if ra < rb else 1
    if isinstance(a, list):
        for x, y in zip(a, b):
            c = _order_cmp(x, y)
            if c:
                return c
        return (len(a) > len(b)) - (len(a) < len(b))
    if isinstance(a, _NodeRef):
        a, b = a.id, b.id
    elif isinstance(a, _RelRef):
        a, b = str(a.key), str(b.key)
    elif isinstance(a, dict):
        return 0
    try:
        return (a > b) - (a < b)
    except TypeError:
        return 0


def _cypher_int_div(a: int, b: int) -> int:
    if b == 0:
        raise NxCypherError("Cypher: / by zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _arith(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    if op == "+":
        if isinstance(a, str) or isinstance(b, str):
            if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
                raise NxCypherError("Cypher: cannot add a string and a collection")
            return _to_string(a) + _to_string(b)
        if isinstance(a, list) or isinstance(b, list):
            return (a if isinstance(a, list) else [a]) + (b if isinstance(b, list) else [b])
    if not (_is_num(a) and _is_num(b)):
        raise NxCypherError(f"Cypher: cannot apply {op} to {a!r} and {b!r}")
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if isinstance(a, int) and isinstance(b, int):
            return _cypher_int_div(a, b)
        return a / b if b else (math.copysign(math.inf, a) if a else math.nan)
    if op == "%":
        if isinstance(a, int) and isinstance(b, int):
            return a - b * _cypher_int_div(a, b)
        return math.fmod(a, b)
    return float(a) ** float(b)


def _to_string(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return f"{v:.1f}"
    return str(v)


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if _is_num(v):
        return int(v)
    try:
        return int(float(str(v).strip()))
    except ValueError:
        return None


def _to_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, str):
        return {"true": True, "false": False}.get(v.strip().lower())
    return None


def _str_fn(fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(v: Any, *args: Any) -> Any:
        if v is None or any(a is None for a in args):
            return None
        if not isinstance(v, str):
            raise NxCypherError(f"Cypher: expected a string, got {v!r}")
        return fn(v, *args)

    return wrapper


def _num_fn(fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(v: Any, *args: Any) -> Any:
        if v is None:
            return None
        if not _is_num(v):
            raise NxCypherError(f"Cypher: expected a number, got {v!r}")
        return fn(v, *args)

    return wrapper


def _size(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (str, list)):
        return len(v)
    raise NxCypherError(f"Cypher: size() expects a string or a list, got {v!r}")


def _substring(s: str, start: int, length: Optional[int] = None) -> str:
    return s[start:] if length is None else s[start:start + length]


def _round(v: Any, precision: int = 0) -> float:
    q = 10 ** precision
    return float(math.floor(v * q + 0.5) / q)  # half up, like Neo4j


# Functions needing the graph receive it as first argument.
_GRAPH_FUNCTIONS = {"id", "elementid", "labels", "type", "keys", "properties", "startnode", "endnode"}

_SCALAR_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "coalesce": lambda *args: next((a for a in args if a is not None), None),
    "tolower": _str_fn(str.lower),
    "lower": _str_fn(str.lower),
    "toupper": _str_fn(str.upper),
    "upper": _str_fn(str.upper),
    "trim": _str_fn(str.strip),
    "ltrim": _str_fn(str.lstrip),
    "rtrim": _str_fn(str.rstrip),
    "replace": _str_fn(lambda s, a, b: s.replace(a, b)),
    "substring": _str_fn(_substring),
    "left": _str_fn(lambda s, n: s[:n]),
    "right": _str_fn(lambda s, n: s[-n:] if n else ""),
    "split": _str_fn(lambda s, sep: s.split(sep)),
    "reverse": lambda v: None if v is None else v[::-1],
    "size": _size,
    "length": _size,
    "head": lambda v: v[0] if v else None,
    "last": lambda v: v[-1] if v else None,
    "tostring": _to_string,
    "tointeger": _to_int,
    "tofloat": _to_float,
    "toboolean": _to_bool,
    "abs": _num_fn(abs),
    "ceil": _num_fn(lambda v: float(math.ceil(v))),
    "floor": _num_fn(lambda v: float(math.floor(v))),
    "round": _num_fn(_round),
    "sqrt": _num_fn(lambda v: math.sqrt(v) if v >= 0 else math.nan),
    "sign": _num_fn(lambda v: (v > 0) - (v < 0)),
    "exists": lambda v: v is not None,
    **{name: None for name in _GRAPH_FUNCTIONS},  # handled in _Evaluator.call
}


# ----------------------------------------------------------------------
# Evaluation
# ----------------------------------------------------------------------


def _contains_aggregate(expr: tuple) -> bool:
    if expr[0] == "countstar":
        return True
    if expr[0] == "call" and expr[1] in _AGGREGATES:
        return True
    return any(_contains_aggregate(x) for x in _children(expr))


def _children(expr: tuple) -> List[tuple]:
    kind = expr[0]
    if kind in ("prop", "not", "neg", "isnull", "haslabels"):
        return [expr[1]]
    if kind in ("and", "or", "xor", "index"):
        return [expr[1], expr[2]]
    if kind in ("cmp", "arith"):
        return [expr[2], expr[3]]
    if kind == "list":
        return list(expr[1])
    if kind == "map":
        return [e for _, e in expr[1]]
    if kind == "call":
        return list(expr[2])
    return []  # lit, param, var, countstar


class _Evaluator:
    def __init__(self, graph: Any, params: Dict[str, Any]) -> None:
        self.graph = graph
        self.params = params

    # -- graph access --------------------------------------------------

    def node_attrs(self, nid: Any) -> Dict[str, Any]:
        return self.graph._node_attrs.get(nid, {})

    def node_labels(self, nid: Any) -> List[str]:
        attrs = self.node_attrs(nid)
        return list(attrs.get("labels") or [attrs.get("main_label", "Node")])

    def rel_attrs(self, key: Tuple[Any, Any, str]) -> Dict[str, Any]:
        return self.graph._edge_attrs.get(key, {})

    def to_output(self, v: Any) -> Any:
        if isinstance(v, _NodeRef):
            return {"labels": self.node_labels(v.id), "properties": dict(self.node_attrs(v.id))}
        if isinstance(v, _RelRef):
            return {
                "type": v.key[2],
                "start_node": v.key[0],
                "end_node": v.key[1],
                "properties": dict(self.rel_attrs(v.key)),
            }
        if isinstance(v, list):
            return [self.to_output(x) for x in v]
        if isinstance(v, dict):
            return {k: self.to_output(x) for k, x in v.items()}
        return v

    # -- expressions ---------------------------------------------------

    def eval(self, expr: tuple, row: Dict[str, Any], group: Optional[List[Dict[str, Any]]] = None) -> Any:
        kind = expr[0]
        if kind == "lit":
            return expr[1]
        if kind == "param":
            if expr[1] not in self.params:
                raise NxCypherError(f"Missing parameter: {expr[1]}")
            return self.params[expr[1]]
        if kind == "var":
            if expr[1] not in row:
                raise NxCypherError(f"Cypher: variable `{expr[1]}` not defined")
            return row[expr[1]]
        if kind == "prop":
            return self.property(self.eval(expr[1], row, group), expr[2])
        if kind == "list":
            return [self.eval(x, row, group) for x in expr[1]]
        if kind == "map":
            return {k: self.eval(x, row, group) for k, x in expr[1]}
        if kind == "index":
            return self.index(self.eval(expr[1], row, group), self.eval(expr[2], row, group))
        if kind == "not":
            v = _truth(self.eval(expr[1], row, group))
            return None if v is None else not v
        if kind in ("and", "or", "xor"):
            a = _truth(self.eval(expr[1], row, group))
            if kind == "and" and a is False:
                return False
            if kind == "or" and a is True:
                return True
            b = _truth(self.eval(expr[2], row, group))
            if kind == "and":
                return False if b is False else (None if a is None or b is None else True)
            if kind == "or":
                return True if b is True else (None if a is None or b is None else False)
            return None if a is None or b is None else a != b
        if kind == "neg":
            v = self.eval(expr[1], row, group)
            if v is None:
                return None
            if not _is_num(v):
                raise NxCypherError(f"Cypher: cannot negate {v!r}")
            return -v
        if kind == "cmp":
            return self.compare(expr[1], self.eval(expr[2], row, group), self.eval(expr[3], row, group))
        if kind == "isnull":
            is_null = self.eval(expr[1], row, group) is None
            return not is_null if expr[2] else is_null
        if kind == "haslabels":
            v = self.eval(expr[1], row, group)
            if v is None:
                return None
            if not isinstance(v, _NodeRef):
                raise NxCypherError("Cypher: label predicates apply to nodes only")
            return set(expr[2]) <= set(self.node_labels(v.id))
        if kind == "arith":
            return _arith(expr[1], self.eval(expr[2], row, group), self.eval(expr[3], row, group))
        if kind == "countstar":
            return self.aggregate(expr, group)
        if kind == "call":
            if expr[1] in _AGGREGATES:
                return self.aggregate(expr, group)
            return self.call(expr[1], [self.eval(a, row, group) for a in expr[2]])
        raise NxCypherError(f"Cypher: unsupported expression {kind}")  # pragma: no cover

    def property(self, target: Any, key: str) -> Any:
        if target is None:
            return None
        if isinstance(target, _NodeRef):
            return self.node_attrs(target.id).get(key)
        if isinstance(target, _RelRef):
            return self.rel_attrs(target.key).get(key)
        if isinstance(target, dict):
            return target.get(key)
        raise NxCypherError(f"Cypher: cannot read property {key!r} of {target!r}")

    @staticmethod
    def index(target: Any, idx: Any) -> Any:
        if target is None or idx is None:
            return None
        if isinstance(target, list) and isinstance(idx, int) and not isinstance(idx, bool):
            return target[idx] if -len(target) <= idx < len(target) else None
        if isinstance(target, dict) and isinstance(idx, str):
            return target.get(idx)
        raise NxCypherError(f"Cypher: cannot index {target!r} with {idx!r}")

    def compare(self, op: str, a: Any, b: Any) -> Optional[bool]:
        if op == "=":
            return _equals(a, b)
        if op == "<>":
            eq = _equals(a, b)
            return None if eq is None else not eq
        if op in ("<", "<=", ">", ">="):
            return _compare(op, a, b)
        if op == "IN":
            if b is None:
                return None
            if not isinstance(b, list):
                raise NxCypherError("Cypher: IN expects a list")
            saw_null = False
            for item in b:
                eq = _equals(a, item)
                if eq:
                    return True
                if eq is None:
                    saw_null = True
            return None if saw_null else False
        if a is None or b is None:
            return None
        if not (isinstance(a, str) and isinstance(b, str)):
            return None
        if op == "STARTS":
            return a.startswith(b)
        if op == "ENDS":
            return a.endswith(b)
        if op == "CONTAINS":
            return b in a
        try:
            return re.fullmatch(b, a) is not None  # =~
        except re.error as exc:
            raise NxCypherError(f"Cypher: invalid regular expression {b!r}: {exc}") from exc

    def call(self, name: str, args: List[Any]) -> Any:
        if name in _GRAPH_FUNCTIONS:
            if len(args) != 1:
                raise NxCypherError(f"Cypher: {name}() takes exactly one argument")
            v = args[0]
            if v is None:
                return None
            if name in ("id", "elementid"):
                ref = v.id if isinstance(v, _NodeRef) else v.key if isinstance(v, _RelRef) else None
                if ref is None:
                    raise NxCypherError(f"Cypher: {name}() expects a node or relationship")
                return ref if name == "id" else str(ref)
            if name == "labels":
                if not isinstance(v, _NodeRef):
                    raise NxCypherError("Cypher: labels() expects a node")
                return self.node_labels(v.id)
            if name in ("type", "startnode", "endnode"):
                if not isinstance(v, _RelRef):
                    raise NxCypherError(f"Cypher: {name}() expects a relationship")
                return v.key[2] if name == "type" else _NodeRef(v.key[0] if name == "startnode" else v.key[1])
            attrs = (
                self.node_attrs(v.id) if isinstance(v, _NodeRef)
                else self.rel_attrs(v.key) if isinstance(v, _RelRef)
                else v if isinstance(v, dict) else None
            )
            if attrs is None:
                raise NxCypherError(f"Cypher: {name}() expects a node, relationship or map")
            return list(attrs) if name == "keys" else dict(attrs)
        try:
            return _SCALAR_FUNCTIONS[name](*args)
        except TypeError as exc:
            raise NxCypherError(f"Cypher: bad arguments for {name}(): {exc}") from exc

    def aggregate(self, expr: tuple, group: Optional[List[Dict[str, Any]]]) -> Any:
        if group is None:
            raise NxCypherError("Cypher: aggregation is not allowed here")
        if expr[0] == "countstar":
            return len(group)
        name, args, distinct = expr[1], expr[2], expr[3]
        if len(args) != 1:
            raise NxCypherError(f"Cypher: {name}() takes exactly one argument")
        if _contains_aggregate(args[0]):
            raise NxCypherError("Cypher: nested aggregations are not allowed")
        values = [self.eval(args[0], row) for row in group]
        values = [v for v in values if v is not None]
        if distinct:
            seen: Set[Any] = set()
            unique = []
            for v in values:
                h = _hashable(v)
                if h not in seen:
                    seen.add(h)
                    unique.append(v)
            values = unique
        if name == "count":
            return len(values)
        if name == "collect":
            return values
        if name in ("sum", "avg"):
            if any(not _is_num(v) for v in values):
                raise NxCypherError(f"Cypher: {name}() expects numbers")
            if name == "sum":
                return sum(values) if values else 0
            return sum(values) / len(values) if values else None
        if not values:
            return None
        key = functools.cmp_to_key(_order_cmp)
        return min(values, key=key) if name == "min" else max(values, key=key)

    # -- clauses -------------------------------------------------------

    def run(self, clauses: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = [{}]
        for clause in clauses:
            if isinstance(clause, _Match):
                rows = self.match(clause, rows)
            else:
                rows = self.project(clause, rows)
        return [{k: self.to_output(v) for k, v in row.items()} for row in rows]

    def match(self, clause: _Match, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        new_vars = self._pattern_vars(clause)
        for row in rows:
            found = False
            for extended in self._match_patterns(clause.patterns, 0, row, set()):
                if clause.where is None or _truth(self.eval(clause.where, extended)) is True:
                    out.append(extended)
                    found = True
            if clause.optional and not found:
                out.append({**row, **{v: None for v in new_vars if v not in row}})
        return out

    @staticmethod
    def _pattern_vars(clause: _Match) -> List[str]:
        names = []
        for first, chain in clause.patterns:
            names.append(first.var)
            for rel, node in chain:
                names += [rel.var, node.var]
        return names

    def _match_patterns(self, patterns: list, idx: int, row: Dict[str, Any],
                        used: Set[Any]) -> Iterator[Dict[str, Any]]:
        if idx == len(patterns):
            yield row
            return
        first, chain = patterns[idx]
        for nid in self._node_candidates(first, row):
            bound = {**row, first.var: _NodeRef(nid)}
            for extended, used2 in self._expand(chain, 0, nid, bound, used):
                yield from self._match_patterns(patterns, idx + 1, extended, used2)

    def _node_candidates(self, pat: _NodePat, row: Dict[str, Any]) -> Iterator[Any]:
        if pat.var in row:
            v = row[pat.var]
            if v is None:
                return
            if not isinstance(v, _NodeRef):
                raise NxCypherError(f"Cypher: `{pat.var}` is not a node")
            if self._node_ok(v.id, pat, row):
                yield v.id
            return
        for nid in list(self.graph._node_attrs):
            if self._node_ok(nid, pat, row):
                yield nid

    def _node_ok(self, nid: Any, pat: _NodePat, row: Dict[str, Any]) -> bool:
        if pat.labels and not set(pat.labels) <= set(self.node_labels(nid)):
            return False
        attrs = self.node_attrs(nid)
        return all(_equals(attrs.get(k), self.eval(e, row)) is True for k, e in pat.props)

    def _expand(self, chain: list, idx: int, nid: Any, row: Dict[str, Any],
                used: Set[Any]) -> Iterator[Tuple[Dict[str, Any], Set[Any]]]:
        if idx == len(chain):
            yield row, used
            return
        rel, node = chain[idx]
        g = self.graph._graph
        candidates: List[Tuple[Tuple[Any, Any, str], Any]] = []
        if rel.direction in ("out", "both"):
            candidates += [((u, v, k), v) for u, v, k in g.out_edges(nid, keys=True)]
        if rel.direction in ("in", "both"):
            candidates += [
                ((u, v, k), u) for u, v, k in g.in_edges(nid, keys=True)
                if rel.direction == "in" or u != v  # a self-loop is listed once
            ]
        bound_rel = row.get(rel.var) if rel.var in row else None
        for key, other in candidates:
            if key in used:
                continue
            if rel.var in row and bound_rel != _RelRef(key):
                continue
            if rel.types and key[2] not in rel.types:
                continue
            attrs = self.rel_attrs(key)
            if not all(_equals(attrs.get(k), self.eval(e, row)) is True for k, e in rel.props):
                continue
            if node.var in row:
                target = row[node.var]
                if not isinstance(target, _NodeRef) or target.id != other:
                    continue
            if not self._node_ok(other, node, row):
                continue
            new_row = {**row, rel.var: _RelRef(key), node.var: _NodeRef(other)}
            yield from self._expand(chain, idx + 1, other, new_row, used | {key})

    def project(self, clause: _Projection, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = list(clause.items)
        if clause.star:
            star_vars = sorted({k for r in rows for k in r if not k.startswith("  ")})
            if not star_vars and not items and rows:
                raise NxCypherError("Cypher: RETURN * with no variables in scope")
            items = [(("var", v), v, v) for v in star_vars] + items

        aggregating = any(_contains_aggregate(expr) for expr, _, _ in items)
        # (projected row, scope for ORDER BY)
        projected: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        if aggregating:
            keys = [(expr, alias) for expr, alias, _ in items if not _contains_aggregate(expr)]
            groups: Dict[Any, List[Dict[str, Any]]] = {}
            for row in rows:
                gkey = tuple(_hashable(self.eval(expr, row)) for expr, _ in keys)
                groups.setdefault(gkey, []).append(row)
            if not groups and not keys:
                groups[()] = []
            for group in groups.values():
                first = group[0] if group else {}
                out = {alias: self.eval(expr, first, group) for expr, alias, _ in items}
                projected.append((out, dict(out)))
        else:
            for row in rows:
                out = {alias: self.eval(expr, row) for expr, alias, _ in items}
                projected.append((out, {**row, **out}))

        if clause.distinct:
            seen: Set[Any] = set()
            unique = []
            for out, scope in projected:
                h = tuple((k, _hashable(v)) for k, v in out.items())
                if h not in seen:
                    seen.add(h)
                    unique.append((out, dict(out) if aggregating else scope))
            projected = unique

        if clause.order:
            by_text = {text: alias for _, alias, text in items}

            def sort_values(entry: Tuple[Dict[str, Any], Dict[str, Any]]) -> List[Any]:
                out, scope = entry
                values = []
                for expr, text, _ in clause.order:
                    if text in by_text:
                        values.append(out[by_text[text]])
                    elif aggregating and _contains_aggregate(expr):
                        raise NxCypherError(
                            "Cypher: ORDER BY an aggregation must use an expression from the projection"
                        )
                    else:
                        values.append(self.eval(expr, scope))
                return values

            decorated = [(sort_values(e), e) for e in projected]

            def cmp(x: Tuple[List[Any], Any], y: Tuple[List[Any], Any]) -> int:
                for (_, _, desc), a, b in zip(clause.order, x[0], y[0]):
                    c = _order_cmp(a, b)
                    if c:
                        return -c if desc else c
                return 0

            decorated.sort(key=functools.cmp_to_key(cmp))
            projected = [e for _, e in decorated]

        result = [out for out, _ in projected]
        if clause.skip is not None:
            result = result[self._count(clause.skip, "SKIP"):]
        if clause.limit is not None:
            result = result[:self._count(clause.limit, "LIMIT")]
        if clause.where is not None:
            result = [r for r in result if _truth(self.eval(clause.where, r)) is True]
        return result

    def _count(self, expr: tuple, what: str) -> int:
        v = self.eval(expr, {})
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise NxCypherError(f"Cypher: {what} expects a non-negative integer, got {v!r}")
        return v


def execute_read(graph: Any, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Run a read-only Cypher query on a ``NetworkXGraph``.

    Raises:
        NxCypherError: The query is invalid, uses unsupported syntax, or
            references a missing parameter.
    """
    clauses = _Parser(cypher).parse()
    return _Evaluator(graph, params or {}).run(clauses)
