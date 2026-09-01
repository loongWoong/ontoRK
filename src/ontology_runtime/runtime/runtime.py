"""Runtime: the unified entry point of the kernel.

    describe / get / find / related / invoke

The runtime owns the execution pipeline:

    FunctionCall -> resolve def -> resolve target -> build context
        -> execute -> collect mutations -> validate -> apply
        -> change -> FunctionResult
"""

from __future__ import annotations

import uuid

from ..core.errors import (
    InvalidArgument,
    KernelError,
    ObjectNotFound,
    RelationNotFound,
    SchemaViolation,
)
from ..core.execution import (
    MUTATION_LINK,
    MUTATION_SET_PROPERTY,
    MUTATION_UNLINK,
    Change,
    ExecutionContext,
    FunctionCall,
    FunctionResult,
    Mutation,
)
from ..core.refs import ObjectRef
from ..core.schema import CapabilityCatalog, FunctionDef, OntologySchema
from ..core.state import Object
from ..ports.function_executor import FunctionExecutor
from ..ports.state_store import StateStore

DIRECTIONS = ("out", "in")
_FILTER_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in")


class OntologyRuntime:
    """Semantic object runtime.  Composed of schema + catalog + ports."""

    def __init__(
        self,
        schema: OntologySchema,
        catalog: CapabilityCatalog,
        store: StateStore,
        executor: FunctionExecutor,
    ) -> None:
        self.schema = schema
        self.catalog = catalog
        self.store = store
        self.executor = executor

    # ------------------------------------------------------------------
    # introspection (describe is index-level; details on demand)
    # ------------------------------------------------------------------

    def describe(self) -> dict:
        return {
            "ontology": {
                "name": self.schema.name,
                "version": self.schema.version,
                "namespace": self.schema.namespace,
            },
            "objects": [
                {"name": o.name, "description": o.description}
                for o in self.schema.objects.values()
            ],
            "relations": [
                {
                    "name": r.name,
                    "source": r.source,
                    "target": r.target,
                    "cardinality": r.cardinality,
                }
                for r in self.schema.relations.values()
            ],
            "functions": [
                {
                    "name": f.name,
                    "target": f.target_type,
                    "description": f.description,
                }
                for f in self.catalog.functions.values()
            ],
        }

    def describe_object(self, name: str) -> dict:
        otype = self._object_type(name)
        return {
            "name": otype.name,
            "description": otype.description,
            "properties": {
                p_name: {"type": p.type.kind, "description": p.description}
                for p_name, p in otype.properties.items()
            },
            "relations_out": [
                r.name for r in self.schema.relations.values() if r.source == name
            ],
            "relations_in": [
                r.name for r in self.schema.relations.values() if r.target == name
            ],
            "functions": [
                f.name for f in self.catalog.functions.values() if f.target_type == name
            ],
        }

    def describe_relation(self, name: str) -> dict:
        r = self._relation_def(name)
        return {
            "name": r.name,
            "source": r.source,
            "target": r.target,
            "cardinality": r.cardinality,
        }

    def describe_function(self, name: str) -> dict:
        f = self.catalog.get(name)
        return {
            "name": f.name,
            "target": f.target_type,
            "description": f.description,
            "inputs": {k: t.kind for k, t in f.inputs.items()},
        }

    # ------------------------------------------------------------------
    # state read
    # ------------------------------------------------------------------

    def get(self, ref: ObjectRef) -> Object:
        obj = self.store.get_object(ref)
        if obj is None:
            raise ObjectNotFound(f"object not found: {ref}")
        return obj

    def find(self, object_type: str, **filters: object) -> list[Object]:
        self._object_type(object_type)  # unknown type -> InvalidArgument
        self._check_filters(object_type, filters)
        return self.store.find_objects(object_type, dict(filters))

    def related(
        self, ref: ObjectRef, relation: str, direction: str = "out"
    ) -> list[ObjectRef]:
        if direction not in DIRECTIONS:
            raise InvalidArgument(
                f"direction must be one of {DIRECTIONS}, got {direction!r}"
            )
        self._relation_def(relation)
        if self.store.get_object(ref) is None:
            raise ObjectNotFound(f"object not found: {ref}")
        return self.store.related(ref, relation, direction)

    # ------------------------------------------------------------------
    # capability execution
    # ------------------------------------------------------------------

    def invoke(
        self,
        function: str,
        target: ObjectRef | None = None,
        args: dict[str, object] | None = None,
    ) -> FunctionResult:
        """Execute a business capability.

        ``invoke`` never raises: every failure - unknown function, invalid
        arguments, missing target, business error, schema violation - is
        reported through ``FunctionResult.error``.

        The executor only runs the function; the runtime then validates and
        applies the buffered mutations as one atomic unit.  A failed
        invocation never leaves partial state behind.
        """
        execution_id = f"exec-{uuid.uuid4().hex[:12]}"
        try:
            fdef = self.catalog.get(function)
            args = dict(args or {})
            self._check_args(fdef, args)
            target = self._resolve_target(fdef, target)
        except KernelError as exc:
            return FunctionResult(execution_id=execution_id, error=exc)
        except Exception as exc:  # malformed args container etc.
            return FunctionResult(
                execution_id=execution_id, error=InvalidArgument(str(exc))
            )

        context = ExecutionContext(
            function=fdef.name,
            execution_id=execution_id,
            target=target,
            args=args,
            _get_object=self.store.get_object,
            _find_objects=self._find_for_context,
            _related=self.store.related,
        )
        call = FunctionCall(function=fdef.name, target=target, args=args)
        try:
            result = self.executor.execute(call, context)
        except Exception as exc:  # kernel + business errors become results
            return FunctionResult(execution_id=execution_id, error=exc)

        if result.error is not None:
            return result  # executor already failed; never apply its buffer
        try:
            result.changes = self._apply_mutations(context.take_mutations())
        except Exception as exc:
            # validation failed or store rejected a precondition:
            # nothing has been applied (validate-before-apply)
            return FunctionResult(execution_id=execution_id, error=exc)
        return result

    # ------------------------------------------------------------------
    # pipeline internals
    # ------------------------------------------------------------------

    def _find_for_context(self, object_type: str, filters: dict) -> list[Object]:
        return self.find(object_type, **filters)

    def _apply_mutations(self, mutations: list[Mutation]) -> list[Change]:
        """Validate every mutation first, then apply - the atomic commit
        step of the pipeline.  Only the runtime calls this."""
        for mutation in mutations:
            self._validate_mutation(mutation)
        return self.store.apply(mutations)

    def _validate_mutation(self, m: Mutation) -> None:
        if m.kind == MUTATION_SET_PROPERTY:
            if m.object_ref is None or m.property is None:
                raise SchemaViolation("set_property requires object_ref and property")
            otype = self.schema.objects.get(m.object_ref.type)
            if otype is None:
                raise SchemaViolation(f"unknown object type: {m.object_ref.type!r}")
            pdef = otype.properties.get(m.property)
            if pdef is None:
                raise SchemaViolation(
                    f"{otype.name} has no property {m.property!r}"
                )
            if not pdef.type.check(m.value):
                raise SchemaViolation(
                    f"property {otype.name}.{m.property} expects "
                    f"{pdef.type.kind}, got {type(m.value).__name__}"
                )
        elif m.kind in (MUTATION_LINK, MUTATION_UNLINK):
            if m.relation is None or m.source is None or m.target is None:
                raise SchemaViolation(f"{m.kind} requires source, relation, target")
            rdef = self._relation_def(m.relation)
            if m.source.type != rdef.source:
                raise SchemaViolation(
                    f"relation {rdef.name} source must be {rdef.source}, "
                    f"got {m.source.type}"
                )
            if m.target.type != rdef.target:
                raise SchemaViolation(
                    f"relation {rdef.name} target must be {rdef.target}, "
                    f"got {m.target.type}"
                )
            if self.store.get_object(m.source) is None:
                raise ObjectNotFound(f"object not found: {m.source}")
            if self.store.get_object(m.target) is None:
                raise ObjectNotFound(f"object not found: {m.target}")
        else:
            raise SchemaViolation(f"unknown mutation kind: {m.kind!r}")

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    def _object_type(self, name: str):
        otype = self.schema.objects.get(name)
        if otype is None:
            raise InvalidArgument(f"unknown object type: {name!r}")
        return otype

    def _relation_def(self, name: str):
        rdef = self.schema.relations.get(name)
        if rdef is None:
            raise RelationNotFound(f"relation not found: {name!r}")
        return rdef

    def _resolve_target(
        self, fdef: FunctionDef, target: ObjectRef | None
    ) -> ObjectRef | None:
        if fdef.target_type is None:
            return target
        if target is None:
            raise InvalidArgument(f"function {fdef.name} requires a target object")
        if target.type != fdef.target_type:
            raise InvalidArgument(
                f"function {fdef.name} targets {fdef.target_type}, "
                f"got {target.type}"
            )
        if self.store.get_object(target) is None:
            raise ObjectNotFound(f"object not found: {target}")
        return target

    def _check_args(self, fdef: FunctionDef, args: dict) -> None:
        for key, value in args.items():
            tdef = fdef.inputs.get(key)
            if tdef is None:
                raise InvalidArgument(
                    f"unknown argument {key!r} for {fdef.name}; "
                    f"expected {sorted(fdef.inputs)}"
                )
            if not tdef.check(value):
                raise InvalidArgument(
                    f"argument {key!r} of {fdef.name} expects "
                    f"{tdef.kind}, got {type(value).__name__}"
                )
        missing = [k for k in fdef.inputs if k not in args]
        if missing:
            raise InvalidArgument(f"missing arguments for {fdef.name}: {missing}")

    def _check_filters(self, object_type: str, filters: dict) -> None:
        otype = self.schema.objects[object_type]
        for key in filters:
            prop, _, op = key.partition("__")
            if prop not in otype.properties:
                raise InvalidArgument(
                    f"{object_type} has no property {prop!r}"
                )
            if op and op not in _FILTER_OPS:
                raise InvalidArgument(
                    f"unknown filter operator {op!r}; supported: {_FILTER_OPS}"
                )
