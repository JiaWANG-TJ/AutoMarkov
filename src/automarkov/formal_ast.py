"""决策过程核心语义的类型化 Formal AST。

所有 AST 节点均采用 Pydantic StrictFrozenModel，确保深度不可变性与
可复现的序列化行为。类型别名用于表达式 (Expr)、分布 (Distribution)、
核 (Kernel)、谓词 (Predicate) 的递归联合，通过 model_validator
实现 DiscriminatedUnion 风格的反序列化。
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field

from automarkov.domain.models import StrictFrozenModel

# ---------------------------------------------------------------------------
# Expression AST
# ---------------------------------------------------------------------------


class _ExprBase(StrictFrozenModel):
    """所有表达式节点的基类，提供 kind 判别字段。"""

    kind: str = Field(exclude=True)


class Constant(_ExprBase):
    """字面常量。"""

    kind: Literal["constant"] = "constant"
    value: float | int | str | bool | None


class VariableRef(_ExprBase):
    """对 agent 变量的引用。"""

    kind: Literal["variable_ref"] = "variable_ref"
    name: str
    scope: Literal["agent", "state", "action", "observation", "reward"]


class UnaryOp(_ExprBase):
    """一元运算。"""

    kind: Literal["unary_op"] = "unary_op"
    op: Literal["neg", "abs", "not", "exp", "log", "sqrt"]
    operand: Expr


class BinaryOp(_ExprBase):
    """二元运算。"""

    kind: Literal["binary_op"] = "binary_op"
    op: Literal["add", "sub", "mul", "div", "mod", "pow", "min", "max"]
    left: Expr
    right: Expr


class Compare(_ExprBase):
    """比较表达式，返回布尔值。"""

    kind: Literal["compare"] = "compare"
    op: Literal["lt", "le", "eq", "ge", "gt"]
    left: Expr
    right: Expr


class BooleanOp(_ExprBase):
    """布尔逻辑运算。"""

    kind: Literal["boolean_op"] = "boolean_op"
    op: Literal["and", "or"]
    operands: list[Expr]


class IfThenElse(_ExprBase):
    """条件分支表达式。"""

    kind: Literal["if_then_else"] = "if_then_else"
    condition: Expr
    then_branch: Expr
    else_branch: Expr


class Lookup(_ExprBase):
    """根据键查表。"""

    kind: Literal["lookup"] = "lookup"
    table_name: str
    key: Expr


class Aggregate(_ExprBase):
    """对表达式的聚合运算。"""

    kind: Literal["aggregate"] = "aggregate"
    fn: Literal["sum", "mean", "min", "max", "count", "any", "all"]
    over: Expr
    filter: Expr | None = None


class Clip(_ExprBase):
    """将表达式裁剪到 [low, high] 区间。"""

    kind: Literal["clip"] = "clip"
    operand: Expr
    low: Expr
    high: Expr


class Indicator(_ExprBase):
    """指示函数：谓词为真时在给定范围内返回 1，否则返回 0。"""

    kind: Literal["indicator"] = "indicator"
    predicate: Predicate
    within: Expr


# 表达式联合类型别名，通过 model_validator 实现鉴别反序列化。
Expr: TypeAlias = (
    Constant
    | VariableRef
    | UnaryOp
    | BinaryOp
    | Compare
    | BooleanOp
    | IfThenElse
    | Lookup
    | Aggregate
    | Clip
    | Indicator
)


# ---------------------------------------------------------------------------
# 分布 AST
# ---------------------------------------------------------------------------


class _DistributionBase(StrictFrozenModel):
    """所有分布节点的基类，提供 kind 别字段。"""

    kind: str = Field(exclude=True)


class Deterministic(_DistributionBase):
    """退化分布：以概率 1 取确定值。"""

    kind: Literal["deterministic"] = "deterministic"
    value: Expr


class Categorical(_DistributionBase):
    """有限结果上的分类分布。"""

    kind: Literal["categorical"] = "categorical"
    outcomes: list[tuple[Expr, float]]


class Bernoulli(_DistributionBase):
    """参数为 p 的伯努利分布。"""

    kind: Literal["bernoulli"] = "bernoulli"
    p: Expr


class Normal(_DistributionBase):
    """正态分布 N(mean, stddev)。"""

    kind: Literal["normal"] = "normal"
    mean: Expr
    stddev: Expr


class TruncatedNormal(_DistributionBase):
    """截断正态分布。"""

    kind: Literal["truncated_normal"] = "truncated_normal"
    mean: Expr
    stddev: Expr
    low: Expr
    high: Expr


class Empirical(_DistributionBase):
    """经验分布：采样集合上的加权经验测度。"""

    kind: Literal["empirical"] = "empirical"
    samples: list[Expr]
    weights: list[float]


class ExternalDistributionRef(_DistributionBase):
    """对外部环境或文献中定义的分布的引用。"""

    kind: Literal["external_distribution_ref"] = "external_distribution_ref"
    ref_id: str
    source: Literal["environment", "literature"]


# 分布联合类型别名。
Distribution: TypeAlias = (
    Deterministic
    | Categorical
    | Bernoulli
    | Normal
    | TruncatedNormal
    | Empirical
    | ExternalDistributionRef
)


# ---------------------------------------------------------------------------
# Kernel AST
# ---------------------------------------------------------------------------


class _KernelBase(StrictFrozenModel):
    """所有核节点的基类，提供 kind 别字段。"""

    kind: str = Field(exclude=True)


class DeterministicAssignmentKernel(_KernelBase):
    """确定性赋值核：将表达式赋给变量。"""

    kind: Literal["deterministic_assignment"] = "deterministic_assignment"
    variable: str
    expression: Expr


class FactorizedStochasticKernel(_KernelBase):
    """因式化随机核：从分布中采样赋给变量。"""

    kind: Literal["factorized_stochastic"] = "factorized_stochastic"
    variable: str
    distribution: Distribution


class JointStochasticKernel(_KernelBase):
    """联合随机核：从联合分布中同时采样多个变量。"""

    kind: Literal["joint_stochastic"] = "joint_stochastic"
    assignments: list[tuple[str, Distribution]]


class ExternalKernelRef(_KernelBase):
    """对外部定义的核的引用。"""

    kind: Literal["external_kernel_ref"] = "external_kernel_ref"
    ref_id: str
    source: Literal["environment", "literature"]


# 核联合类型别名。
Kernel: TypeAlias = (
    DeterministicAssignmentKernel
    | FactorizedStochasticKernel
    | JointStochasticKernel
    | ExternalKernelRef
)


# ---------------------------------------------------------------------------
# Predicate AST
# ---------------------------------------------------------------------------


class _PredicateBase(StrictFrozenModel):
    """所有谓词节点的基类，提供 kind 别字段。"""

    kind: str = Field(exclude=True)


class ComparisonPredicate(_PredicateBase):
    """比较谓词：两个表达式之间的序关系。"""

    kind: Literal["comparison"] = "comparison"
    op: Literal["lt", "le", "eq", "ge", "gt"]
    left: Expr
    right: Expr


class LogicalPredicate(_PredicateBase):
    """逻辑连接谓词。"""

    kind: Literal["logical"] = "logical"
    op: Literal["and", "or", "not"]
    operands: list[Predicate]


class QuantifiedFinitePredicate(_PredicateBase):
    """有限域上的量化谓词。"""

    kind: Literal["quantified_finite"] = "quantified_finite"
    quantifier: Literal["forall", "exists"]
    variable: str
    domain: list[Expr]
    body: Predicate


# 谓词联合类型别名。
Predicate: TypeAlias = (
    ComparisonPredicate
    | LogicalPredicate
    | QuantifiedFinitePredicate
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_EXPR_KINDS = {
    "constant",
    "variable_ref",
    "unary_op",
    "binary_op",
    "compare",
    "boolean_op",
    "if_then_else",
    "lookup",
    "aggregate",
    "clip",
    "indicator",
}

_DISTRIBUTION_KINDS = {
    "deterministic",
    "categorical",
    "bernoulli",
    "normal",
    "truncated_normal",
    "empirical",
    "external_distribution_ref",
}

_KERNEL_KINDS = {
    "deterministic_assignment",
    "factorized_stochastic",
    "joint_stochastic",
    "external_kernel_ref",
}

_PREDICATE_KINDS = {
    "comparison",
    "logical",
    "quantified_finite",
}


def _validate_node(
    node: object,
    *,
    allowed_kinds: set[str],
    kind_label: str,
    ctx: object,
) -> None:
    """递归验证 AST 节点的结构完整性。"""

    if not isinstance(node, BaseModel):
        raise TypeError(f"{kind_label} must be a model instance")
    kind = getattr(node, "kind", None)
    if kind not in allowed_kinds:
        raise ValueError(
            f"unexpected {kind_label} kind: {kind!r}"
        )
    children: list[object] = []
    for field_name in type(node).model_fields:
        field_value = getattr(node, field_name)
        if field_name == "kind":
            continue
        if isinstance(field_value, list):
            for item in field_value:
                if isinstance(item, tuple):
                    children.extend(item)
                elif isinstance(item, BaseModel):
                    children.append(item)
        elif isinstance(field_value, BaseModel):
            children.append(field_value)
    for child in children:
        _validate_node(
            child,
            allowed_kinds=allowed_kinds,
            kind_label=kind_label,
            ctx=ctx,
        )


def _validate_expr(node: object) -> None:
    """验证表达式 AST 节点。"""
    _validate_node(node, allowed_kinds=_EXPR_KINDS, kind_label="expr", ctx=None)


def _validate_distribution(node: object) -> None:
    """验证分布 AST 节点。"""
    _validate_node(
        node, allowed_kinds=_DISTRIBUTION_KINDS, kind_label="distribution", ctx=None,
    )


def _validate_kernel(node: object) -> None:
    """验证核 AST 节点。"""
    _validate_node(
        node, allowed_kinds=_KERNEL_KINDS, kind_label="kernel", ctx=None,
    )


def _validate_predicate(node: object) -> None:
    """验证谓词 AST 节点。"""
    _validate_node(
        node, allowed_kinds=_PREDICATE_KINDS, kind_label="predicate", ctx=None,
    )


def validate_ast(node: object) -> list[str]:
    """验证 AST 节点并返回错误列表（空列表表示有效）。"""
    kind = getattr(node, "kind", None)
    errors: list[str] = []
    if kind is None:
        errors.append("AST node must have a 'kind' field")
        return errors
    for label, kinds, validator in (
        ("expr", _EXPR_KINDS, _validate_expr),
        ("distribution", _DISTRIBUTION_KINDS, _validate_distribution),
        ("kernel", _KERNEL_KINDS, _validate_kernel),
        ("predicate", _PREDICATE_KINDS, _validate_predicate),
    ):
        if kind in kinds:
            try:
                validator(node)
            except ValueError as exc:
                errors.append(f"{label} validation failed: {exc}")
            return errors
    errors.append(f"unknown AST kind: {kind!r}")
    return errors


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def ast_to_dict(node: object) -> object:
    """递归序列化 AST 节点为 JSON-safe 字典。"""

    if isinstance(node, BaseModel):
        result: dict[str, object] = {}
        for field_name in type(node).model_fields:
            value = getattr(node, field_name)
            result[field_name] = ast_to_dict(value)
        return result
    if isinstance(node, list):
        return [ast_to_dict(item) for item in node]
    if isinstance(node, tuple):
        return [ast_to_dict(item) for item in node]
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    raise ValueError(f"unsupported AST node type: {type(node).__name__}")


_EXPR_MODEL_MAP: dict[str, type[BaseModel]] = {
    "constant": Constant,
    "variable_ref": VariableRef,
    "unary_op": UnaryOp,
    "binary_op": BinaryOp,
    "compare": Compare,
    "boolean_op": BooleanOp,
    "if_then_else": IfThenElse,
    "lookup": Lookup,
    "aggregate": Aggregate,
    "clip": Clip,
    "indicator": Indicator,
}

_DISTRIBUTION_MODEL_MAP: dict[str, type[BaseModel]] = {
    "deterministic": Deterministic,
    "categorical": Categorical,
    "bernoulli": Bernoulli,
    "normal": Normal,
    "truncated_normal": TruncatedNormal,
    "empirical": Empirical,
    "external_distribution_ref": ExternalDistributionRef,
}

_KERNEL_MODEL_MAP: dict[str, type[BaseModel]] = {
    "deterministic_assignment": DeterministicAssignmentKernel,
    "factorized_stochastic": FactorizedStochasticKernel,
    "joint_stochastic": JointStochasticKernel,
    "external_kernel_ref": ExternalKernelRef,
}

_PREDICATE_MODEL_MAP: dict[str, type[BaseModel]] = {
    "comparison": ComparisonPredicate,
    "logical": LogicalPredicate,
    "quantified_finite": QuantifiedFinitePredicate,
}


def _convert_item(
    item: object,
    *,
    child_map: dict[str, type[BaseModel]] | None,
) -> object:
    """将列表中的单个元素递归转换为 AST 模型实例或原始类型。"""

    if isinstance(item, dict):
        kind = item.get("kind")
        if child_map is not None and isinstance(kind, str) and kind in child_map:
            return dict_to_ast(item)
        return _reconvert_children(item, child_map=child_map)
    if isinstance(item, list):
        return [_convert_item(sub, child_map=child_map) for sub in item]
    return item


def _reconvert_children(
    data: dict[str, object],
    *,
    child_map: dict[str, type[BaseModel]] | None,
) -> dict[str, object]:
    """递归将子节点字典转换为 AST 模型实例，同时保留非节点字段不变。"""

    result: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            kind = value.get("kind")
            if child_map is not None and isinstance(kind, str) and kind in child_map:
                result[key] = dict_to_ast(value)
            else:
                result[key] = _reconvert_children(value, child_map=child_map)
        elif isinstance(value, list):
            result[key] = [
                _convert_item(item, child_map=child_map) for item in value
            ]
        else:
            result[key] = value
    return result


def dict_to_ast(data: dict[str, object]) -> BaseModel:
    """从 JSON-safe 字典反序列化为 AST 节点。"""

    if not isinstance(data, dict):
        raise TypeError("AST deserialization requires a dict")
    kind = data.get("kind")
    if not isinstance(kind, str):
        raise TypeError("AST node dict must have a 'kind' string")
    for label, kind_map, base_kinds in (
        ("expr", _EXPR_MODEL_MAP, _EXPR_KINDS),
        ("distribution", _DISTRIBUTION_MODEL_MAP, _DISTRIBUTION_KINDS),
        ("kernel", _KERNEL_MODEL_MAP, _KERNEL_KINDS),
        ("predicate", _PREDICATE_MODEL_MAP, _PREDICATE_KINDS),
    ):
        if kind in base_kinds:
            model_cls = kind_map.get(kind)
            if model_cls is None:
                raise ValueError(f"unknown {label} kind: {kind!r}")
            converted = _reconvert_children(data, child_map=kind_map)
            return model_cls.model_validate(converted)
    raise ValueError(f"unknown AST kind: {kind!r}")