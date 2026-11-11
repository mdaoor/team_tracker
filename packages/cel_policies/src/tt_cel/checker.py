"""Static checker over the celpy parse tree: unknown roots/fields/functions and clear type mismatches, named by field.

`celpy` has no checker of its own, so this walks the `lark` tree and infers a type for every sub-expression from the declared
context schema. `dyn` (untyped maps, loop variables, results of unknown calls) is never an error: the checker only rejects what it can
prove wrong, so a valid policy is never refused.
"""

from __future__ import annotations

from typing import Any

from lark import Token, Tree

from tt_cel.errors import CelError
from tt_cel.schema import BUILTINS, FUNCTIONS, ROOTS, Func, Schema

NUM = {"int", "double"}
MACROS = {"exists", "all", "exists_one", "map", "filter"}
STRING_METHODS = {
    "contains": "bool",
    "startsWith": "bool",
    "endsWith": "bool",
    "matches": "bool",
    "size": "int",
}
TIME_METHODS = {
    "getFullYear": "int",
    "getMonth": "int",
    "getDate": "int",
    "getDayOfWeek": "int",
    "getHours": "int",
}
GLOBAL_FUNCS = {"size": "int", "string": "string", "int": "int", "double": "double", "bool": "bool", "duration": "duration", "timestamp": "timestamp", "dyn": "dyn", "type": "dyn"}  # fmt: skip
LITERAL_TYPES = {"INT_LIT": "int", "UINT_LIT": "int", "FLOAT_LIT": "double", "STRING_LIT": "string", "MLSTRING_LIT": "string", "BYTES_LIT": "dyn", "BOOL_LIT": "bool", "NULL_LIT": "null"}  # fmt: skip
REL = {"relation_lt", "relation_le", "relation_gt", "relation_ge"}
EQ = {"relation_eq", "relation_ne"}


def _tokens(node: Any) -> list[Token]:
    return [c for c in node.children if isinstance(c, Token)] if isinstance(node, Tree) else []


def _text(node: Any) -> str:
    """A readable path for member chains (`item.stage.key`), else the node kind."""
    if isinstance(node, Token):
        return str(node)
    if isinstance(node, Tree):
        if node.data == "ident":
            return str(node.children[0])
        if node.data == "literal":
            return str(node.children[0])
        if node.data == "member_dot":
            return f"{_text(node.children[0])}.{node.children[1]}"
        if (
            node.data
            in {
                "member",
                "primary",
                "unary",
                "multiplication",
                "addition",
                "relation",
                "conditionaland",
                "conditionalor",
                "expr",
            }
            and len(node.children) == 1
        ):
            return _text(node.children[0])
        if node.data == "member_dot_arg":
            return f"{_text(node.children[0])}.{node.children[1]}()"
        if node.data == "ident_arg":
            return f"{node.children[0]}()"
    return "expression"


def _pos(node: Any) -> dict[str, int | None]:
    for t in node.scan_values(lambda v: isinstance(v, Token)):
        return {"line": t.line, "column": t.column}
    return {}


class Checker:
    def __init__(self, schema: Schema) -> None:
        self.schema = schema
        self.funcs: dict[tuple[str | None, str], Func] = {(f.receiver, f.name): f for f in schema.functions}
        self.global_funcs = {
            f.name: f for f in schema.functions if f.receiver is None and f.namespace is None
        }
        self.used_funcs: set[str] = set()

    def err(self, msg: str, node: Any, field: str | None = None) -> CelError:
        return CelError(msg, field=field, **_pos(node))

    # ---- types ---------------------------------------------------------------------------------------------------
    @staticmethod
    def compatible(a: str, b: str) -> bool:
        if "dyn" in (a, b) or "null" in (a, b) or a == b:
            return True
        return a in NUM and b in NUM

    def elem(self, t: str) -> str:
        if t.startswith("list<") and t.endswith(">"):
            return t[5:-1]
        return "dyn"

    def check(self, node: Any, env: dict[str, str]) -> str:
        if isinstance(node, Token):
            return LITERAL_TYPES.get(node.type, "dyn")
        d = node.data
        ch = node.children
        if d == "ident":
            name = str(ch[0])
            if name in env:
                return env[name]
            if name in self.schema.roots:
                return self.schema.roots[name]
            raise self.err(
                f"unknown identifier {name!r}; the policy context has: {', '.join(self.schema.roots)}",
                node,
                field=name,
            )
        if d == "literal":
            tok = ch[0]
            return LITERAL_TYPES.get(tok.type, "dyn") if isinstance(tok, Token) else "dyn"
        if d == "member_dot":
            base = self.check(ch[0], env)
            name = str(ch[1])
            return self.field(base, name, node, ch[0])
        if d == "member_dot_arg":
            return self.method(node, env)
        if d == "ident_arg":
            return self.call(node, env)
        if d in {"dot_ident", "dot_ident_arg"}:
            raise self.err("leading-dot names are not supported", node)
        if d in {"member_item", "member_index"}:
            base = self.check(ch[0], env)
            self.check(ch[1], env)
            return self.elem(base) if base.startswith("list<") else "dyn"
        if d == "member_object":
            raise self.err("message construction is not supported in policies", node)
        if d == "list_lit":
            ts: list[str] = []
            for c in ch:
                if isinstance(c, Tree) and c.data == "exprlist":
                    ts += [self.check(e, env) for e in c.children]
            return f"list<{ts[0]}>" if ts and all(t == ts[0] for t in ts) else "list<dyn>"
        if d == "map_lit":
            for c in ch:
                if isinstance(c, Tree):
                    for sub in c.children:
                        self.check(sub, env)
            return "map<string,dyn>"
        if d in {"conditionalor", "conditionaland"} and len(ch) > 1:
            for c in ch:
                t = self.check(c, env)
                if t not in {"bool", "dyn", "null"}:
                    raise self.err(
                        f"{'||' if d == 'conditionalor' else '&&'} needs true/false on both sides; `{_text(c)}` is a {t}",
                        node,
                        field=_text(c),
                    )
            return "bool"
        if (
            d == "unary"
            and len(ch) == 2
            and isinstance(ch[0], Tree)
            and ch[0].data in {"unary_not", "unary_neg"}
        ):
            t = self.check(ch[1], env)
            if ch[0].data == "unary_not":
                if t not in {"bool", "dyn"}:
                    raise self.err(
                        f"`!` needs true/false; `{_text(ch[1])}` is a {t}", node, field=_text(ch[1])
                    )
                return "bool"
            return t
        if (
            d in {"relation", "addition", "multiplication"}
            and len(ch) == 2
            and isinstance(ch[0], Tree)
            and ch[0].data.startswith(d + "_")
        ):
            op, left, right = ch[0].data, ch[0].children[0], ch[1]
            if d == "relation":
                a, b = self.check(left, env), self.check(right, env)
                if op == "relation_in":
                    return "bool"
                if not self.compatible(a, b):
                    raise self.err(
                        f"cannot compare {a} `{_text(left)}` with {b} `{_text(right)}`",
                        node,
                        field=_text(left),
                    )
                if op in REL and (a == "bool" or a.startswith(("list<", "map<"))):
                    raise self.err(f"`{_text(left)}` of type {a} has no order", node, field=_text(left))
                return "bool"
            return self.arith(op, left, right, node, env)
        if d == "expr" and len(ch) == 3:  # c ? a : b
            c = self.check(ch[0], env)
            if c not in {"bool", "dyn"}:
                raise self.err(
                    f"the condition `{_text(ch[0])}` of `?:` must be true/false, not {c}",
                    node,
                    field=_text(ch[0]),
                )
            a, b = self.check(ch[1], env), self.check(ch[2], env)
            return a if a == b else "dyn"
        # pass-through wrappers: expr, relation, addition, multiplication, unary, member, primary, paren_expr, exprlist ...
        out = "dyn"
        for c in ch:
            if isinstance(c, Tree | Token):
                out = self.check(c, env)
        return out

    def field(self, base: str, name: str, node: Any, base_node: Any) -> str:
        if base in self.schema.objects:
            fields = self.schema.objects[base]
            if name not in fields:
                path = f"{_text(base_node)}.{name}"
                near = ", ".join(sorted(fields)[:12])
                raise self.err(
                    f"unknown field {name!r} on {_text(base_node)} (a {base}); its fields: {near}",
                    node,
                    field=path,
                )
            return fields[name]
        if base.startswith("map<") and "," in base:
            return base[base.index(",") + 1 : -1]
        if base in {"dyn", "null"}:
            return "dyn"
        raise self.err(
            f"`{_text(base_node)}` is a {base} and has no field {name!r}",
            node,
            field=f"{_text(base_node)}.{name}",
        )

    def arith(self, op: str, left: Any, right: Any, node: Any, env: dict[str, str]) -> str:
        a, b = self.check(left, env), self.check(right, env)
        if "dyn" in (a, b):
            return "dyn"
        if a in NUM and b in NUM:
            return "double" if "double" in (a, b) else "int"
        if op == "addition_add":
            if a == b == "string":
                return "string"
            if {a, b} == {"timestamp", "duration"}:
                return "timestamp"
            if a == b == "duration":
                return "duration"
            if a.startswith("list<") and a == b:
                return a
        if op == "addition_sub":
            if a == b == "timestamp":
                return "duration"
            if a == "timestamp" and b == "duration":
                return "timestamp"
            if a == b == "duration":
                return "duration"
        if (
            op in {"multiplication_mul", "multiplication_div"}
            and "duration" in (a, b)
            and (a in NUM or b in NUM)
        ):
            return "duration"
        raise self.err(
            f"cannot combine {a} `{_text(left)}` and {b} `{_text(right)}` here", node, field=_text(left)
        )

    def args(self, node: Any) -> list[Any]:
        for c in node.children:
            if isinstance(c, Tree) and c.data == "exprlist":
                return list(c.children)
        return []

    def method(self, node: Any, env: dict[str, str]) -> str:
        recv_node, name = node.children[0], str(node.children[1])
        args = self.args(node)
        recv = self.check(recv_node, env)
        if name in MACROS:
            if not args or not (isinstance(args[0], Tree) and _text(args[0]).isidentifier()):
                raise self.err(f"`{name}` needs a loop variable first: `.{name}(x, ...)`", node)
            var = _text(args[0])
            el = self.elem(recv) if recv.startswith("list<") else "dyn"
            if recv not in {"dyn"} and not recv.startswith(("list<", "map<")):
                raise self.err(
                    f"`.{name}` works on lists, not on {recv} `{_text(recv_node)}`",
                    node,
                    field=_text(recv_node),
                )
            inner = {**env, var: el}
            body = [self.check(a, inner) for a in args[1:]]
            if name in {"exists", "all", "exists_one"}:
                return "bool"
            return recv if name == "filter" else f"list<{body[-1] if body else 'dyn'}>"
        for a in args:
            self.check(a, env)
        if (
            name in STRING_METHODS
            and recv in {"string", "dyn", "list<string>", "list<dyn>", "map<string,dyn>"}
            or (name == "size" and recv.startswith(("list<", "map<")))
        ):
            return STRING_METHODS[name]
        if name in TIME_METHODS:
            return TIME_METHODS[name]
        f = self.funcs.get((recv, name))
        if f is None and recv == "Metrics":
            f = next((x for x in self.schema.functions if x.namespace == "metrics" and x.name == name), None)
        if f is None and recv == "dyn":
            f = next((x for x in self.schema.functions if x.name == name and x.receiver), None)
        if f is None:
            known = sorted(
                {
                    x.name
                    for x in self.schema.functions
                    if x.receiver == recv or (recv == "Metrics" and x.namespace)
                }
            )
            raise self.err(
                f"`{_text(recv_node)}` ({recv}) has no function {name!r}"
                + (f"; available: {', '.join(known)}" if known else ""),
                node,
                field=f"{_text(recv_node)}.{name}()",
            )
        self._arity(f, args, node)
        self.used_funcs.add(f.name)
        return f.returns

    def call(self, node: Any, env: dict[str, str]) -> str:
        name = str(node.children[0])
        args = self.args(node)
        if name == "has":
            if len(args) != 1:
                raise self.err(
                    "`has()` takes exactly one field selection, e.g. has(item.classification.product)", node
                )
            self.check(args[0], env)
            return "bool"
        for a in args:
            self.check(a, env)
        if name in self.global_funcs:
            f = self.global_funcs[name]
            self._arity(f, args, node)
            self.used_funcs.add(name)
            return f.returns
        if name in GLOBAL_FUNCS:
            return GLOBAL_FUNCS[name]
        if name in BUILTINS:
            return "dyn"
        raise self.err(f"unknown function {name!r}", node, field=f"{name}()")

    def _arity(self, f: Func, args: list[Any], node: Any) -> None:
        need = sum(1 for a in f.args if not a.endswith("?"))
        # namespace functions are called as `metrics.fn(args)`; the receiver is not an argument
        if not (need <= len(args) <= len(f.args)):
            raise self.err(
                f"{f.name}() takes {need}"
                + (f"-{len(f.args)}" if need != len(f.args) else "")
                + f" argument(s), got {len(args)}",
                node,
                field=f"{f.name}()",
            )


def check_tree(tree: Tree[Any], schema: Schema, *, expect_bool: bool = True) -> Checker:
    c = Checker(schema)
    t = c.check(tree, {})
    if expect_bool and t not in {"bool", "dyn"}:
        raise CelError(f"a policy condition must evaluate to true/false, but this evaluates to {t}")
    return c


__all__ = ["Checker", "ROOTS", "FUNCTIONS", "check_tree"]
