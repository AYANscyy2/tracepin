"""calculate: the one honest tool."""

import ast
import operator

from pydantic import BaseModel

from tracepin.instrument import traced_tool

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


class CalculateArgs(BaseModel):
    expression: str


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError(f"unsupported expression element: {ast.dump(node)}")


@traced_tool(description="Evaluate an arithmetic expression.")
def calculate(expression: str) -> float:
    tree = ast.parse(expression, mode="eval")
    return float(_eval(tree.body))
