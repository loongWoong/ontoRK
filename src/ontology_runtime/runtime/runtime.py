"""Runtime: the unified entry point of the kernel.

    describe / get / find / related / invoke

The runtime owns the execution pipeline:

    FunctionCall -> resolve def -> resolve target -> build context
        -> execute -> collect mutations -> validate -> apply
        -> change -> FunctionResult

v0.2 adds two bootstrap write doors next to ``invoke``:

    seed(objects, relations)            - load initial state (idempotent)
    apply_delta(objects, relations, mutations)
                                        - one atomic state delta (Projection
                                          -> Kernel ingestion)

Both validate the *whole* input before writing, and both leave the schema
untouched: constraints are enforced here (the runtime is the only component
that knows both schema and store), never inside StateStore.
"""

from __future__ import annotations

import math
import uuid
from decimal import Decimal, InvalidOperation

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
    STATUS_FAILED,
    STATUS_UNKNOWN,
    Change,
    ExecutionContext,
    FunctionCall,
    FunctionResult,
    Mutation,
)
from ..core.refs import ObjectRef
from ..core.schema import CapabilityCatalog, FunctionDef, ObjectType, OntologySchema
from ..core.state import Object, Relation
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
                "profile": self.schema.profile,
                "schema_version": self.schema.schema_version,
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
            "category": otype.category,
            "identity": list(otype.identity),
            "properties": {
                p_name: self._property_descriptor(pdef)
                for p_name, pdef in otype.properties.items()
            },
            "state_machine": self._state_machine_descriptor(otype.state_machine),
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
            "inverse": r.inverse,
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

    def find(
        self, object_type: str, *, report: bool = False, **filters: object
    ) -> list[Object] | dict[str, object]:
        """Find objects of a type matching ``filters``.

        ``report=False`` (default) returns just the matching objects.
        ``report=True`` returns ``{"objects": [...], "excluded_missing": n}``
        where ``excluded_missing`` counts objects of this type that were not
        matched because they lack a property the filter references.  Under
        the open-world reading "missing" is not "does not match", so the
        exclusion quantity is exposed explicitly instead of being silently
        folded into an empty result.  Objects excluded by a *value*
        mismatch are not counted.
        """
        self._object_type(object_type)  # unknown type -> InvalidArgument
        self._check_filters(object_type, filters)
        matched = self.store.find_objects(object_type, dict(filters))
        if not report:
            return matched
        candidates = self.store.find_objects(object_type, {})
        matched_refs = {obj.ref for obj in matched}
        referenced = {key.partition("__")[0] for key in filters}
        excluded_missing = sum(
            1
            for obj in candidates
            if obj.ref not in matched_refs
            and any(prop not in obj.properties for prop in referenced)
        )
        return {"objects": matched, "excluded_missing": excluded_missing}

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

    def count_objects(self) -> int:
        """Number of objects currently in state (cheap, store-level)."""
        return self.store.count_objects()

    # ------------------------------------------------------------------
    # bootstrap writes: seed + atomic delta
    # ------------------------------------------------------------------

    def validate_seed(
        self, objects: list[Object], relations: list[Relation]
    ) -> None:
        """Validate seed data without writing anything; raises on violation."""
        self._prepare_seed(objects, relations)

    def seed(self, objects: list[Object], relations: list[Relation]) -> None:
        """Validate then write seed data.  Existing objects are never
        overwritten (idempotent restart / bootstrap)."""
        prepared, relations = self._prepare_seed(objects, relations)
        self.store.seed(prepared, relations)

    def _prepare_seed(
        self, objects: list[Object], relations: list[Relation]
    ) -> tuple[list[Object], list[Relation]]:
        objects = list(objects or [])
        relations = list(relations or [])
        # seed inserts only what is missing: existing objects are left
        # untouched, so there is nothing to validate for them either
        creates = [o for o in objects if self.store.get_object(o.ref) is None]
        prepared = self._prepare_upserts(creates)
        self._validate_relation_writes(relations, {o.ref for o in creates})
        return prepared, relations

    def apply_delta(
        self,
        objects: list[Object],
        relations: list[Relation],
        mutations: list[Mutation] | None = None,
        *,
        execution_id: str | None = None,
        source: str | None = None,
    ) -> list[Change]:
        """Apply one validated, atomic delta: upsert objects, assert
        relations, apply mutations.  Either everything is written or
        nothing is (validate-before-apply, one store transaction)."""
        objects = list(objects or [])
        relations = list(relations or [])
        mutations = list(mutations or [])
        prepared = self._prepare_upserts(objects, source=source)
        known_refs = {o.ref for o in objects}
        prepared_by_ref = {obj.ref: obj for obj in prepared}
        # the validation view holds the properties every affected object will
        # have once the delta is applied, so mutations can be checked against
        # the state the buffer itself produces
        view: dict[ObjectRef, dict] = {}
        for obj in objects:
            existing = self.store.get_object(obj.ref)
            if existing is None:
                view[obj.ref] = dict(prepared_by_ref[obj.ref].properties)
            else:
                view[obj.ref] = {**existing.properties, **obj.properties}
        pending_out: dict[ObjectRef, set[ObjectRef]] = {}
        pending_in: dict[ObjectRef, set[ObjectRef]] = {}
        self._validate_relation_writes(
            relations, known_refs, pending_out, pending_in
        )
        known = frozenset(known_refs)
        for mutation in mutations:
            if mutation.kind == MUTATION_SET_PROPERTY:
                self._validate_set_property(mutation, view, known)
            elif mutation.kind in (MUTATION_LINK, MUTATION_UNLINK):
                self._validate_relation_mutation(
                    mutation, pending_out, pending_in, known
                )
            else:
                raise SchemaViolation(
                    f"unknown mutation kind: {mutation.kind!r}"
                )
        return self.store.apply_delta(
            prepared,
            relations,
            mutations,
            execution_id=execution_id,
            source=source,
        )

    def delete(
        self, refs: list[ObjectRef], *, execution_id: str | None = None
    ) -> list[Change]:
        """Delete objects and their incident relations atomically.

        Deletion is a *state sync* operation (projection deletion
        propagation), not a capability mutation: it is idempotent, it never
        creates an execution, and it does not apply function-level
        constraint validation - the source of truth already decided the
        object no longer exists.  Returns the unlink/delete changes so
        observers see the removal on the change stream.
        """
        return self.store.delete_objects(list(refs or ()),
                                         execution_id=execution_id)

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
        reported through ``FunctionResult.error`` and ``status``.

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
            return FunctionResult(
                execution_id=execution_id, error=exc, status=STATUS_FAILED
            )
        except Exception as exc:  # malformed args container etc.
            return FunctionResult(
                execution_id=execution_id,
                error=InvalidArgument(str(exc)),
                status=STATUS_FAILED,
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
        except TimeoutError as exc:
            # the function may have run: outcome unknown, state stays atomic
            return FunctionResult(
                execution_id=execution_id, error=exc, status=STATUS_UNKNOWN
            )
        except Exception as exc:  # kernel + business errors become results
            return FunctionResult(
                execution_id=execution_id, error=exc, status=STATUS_FAILED
            )

        if result.error is not None:
            result.status = STATUS_FAILED
            return result  # executor already failed; never apply its buffer
        try:
            result.changes = self._apply_mutations(
                context.take_mutations(), execution_id=execution_id
            )
        except Exception as exc:
            # validation failed or store rejected a precondition:
            # nothing has been applied (validate-before-apply)
            return FunctionResult(
                execution_id=execution_id, error=exc, status=STATUS_FAILED
            )
        result.status = result.status or "succeeded"
        return result

    # ------------------------------------------------------------------
    # pipeline internals
    # ------------------------------------------------------------------

    def _find_for_context(self, object_type: str, filters: dict) -> list[Object]:
        return self.find(object_type, **filters)

    def _apply_mutations(
        self,
        mutations: list[Mutation],
        *,
        execution_id: str | None = None,
    ) -> list[Change]:
        """Validate every mutation first, then apply - the atomic commit
        step of the pipeline.  Only the runtime calls this.

        Validation simulates the whole buffer, so constraints that depend on
        more than one mutation (unique/identity values set on the same object,
        state-machine transitions, cardinality within one buffer) are caught
        before anything is written.
        """
        view: dict[ObjectRef, dict] = {}
        pending_out: dict[ObjectRef, set[ObjectRef]] = {}
        pending_in: dict[ObjectRef, set[ObjectRef]] = {}
        for mutation in mutations:
            if mutation.kind == MUTATION_SET_PROPERTY:
                self._validate_set_property(mutation, view)
            elif mutation.kind in (MUTATION_LINK, MUTATION_UNLINK):
                self._validate_relation_mutation(
                    mutation, pending_out, pending_in
                )
            else:
                raise SchemaViolation(f"unknown mutation kind: {mutation.kind!r}")
        return self.store.apply(mutations, execution_id=execution_id)

    # ---- mutation (write) validation ----

    def _validate_set_property(
        self,
        m: Mutation,
        view: dict[ObjectRef, dict],
        known_refs: frozenset[ObjectRef] = frozenset(),
    ) -> None:
        """Validate a property write against the schema and the state the
        buffer will produce (``view`` holds per-object merged properties)."""
        if m.object_ref is None or m.property is None:
            raise SchemaViolation("set_property requires object_ref and property")
        otype = self.schema.objects.get(m.object_ref.type)
        if otype is None:
            raise SchemaViolation(f"unknown object type: {m.object_ref.type!r}")
        pdef = otype.properties.get(m.property)
        if pdef is None:
            raise SchemaViolation(f"{otype.name} has no property {m.property!r}")
        self._validate_property_value(otype, m.property, pdef, m.value)

        current = view.get(m.object_ref)
        if current is None:
            existing = self.store.get_object(m.object_ref)
            current = existing.properties if existing is not None else None
        if current is None:
            if m.object_ref in known_refs:
                return  # created earlier in the same delta; validated by upsert
            raise ObjectNotFound(f"object not found: {m.object_ref}")

        self._check_state_transitions(otype, current, {m.property: m.value})
        merged = {**current, m.property: m.value}
        self._check_unique_conflicts(otype, merged, m.object_ref, overrides=view)
        view[m.object_ref] = merged

    def _validate_relation_mutation(
        self,
        m: Mutation,
        pending_out: dict[ObjectRef, set[ObjectRef]] | None = None,
        pending_in: dict[ObjectRef, set[ObjectRef]] | None = None,
        known_refs: frozenset[ObjectRef] = frozenset(),
    ) -> None:
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
        for ref in (m.source, m.target):
            if self.store.get_object(ref) is None and ref not in known_refs:
                raise ObjectNotFound(f"object not found: {ref}")
        if m.kind == MUTATION_LINK:
            pending_out = {} if pending_out is None else pending_out
            pending_in = {} if pending_in is None else pending_in
            self._check_cardinality(rdef, m.source, m.target, pending_out, pending_in)
            pending_out.setdefault(m.source, set()).add(m.target)
            pending_in.setdefault(m.target, set()).add(m.source)

    # ---- constraints ----

    def _property_descriptor(self, pdef) -> dict:
        return {
            "type": pdef.type.kind,
            "description": pdef.description,
            "required": pdef.required,
            "unique": pdef.unique,
            "enum": list(pdef.enum) if pdef.enum is not None else None,
            "minimum": pdef.minimum,
            "maximum": pdef.maximum,
            "unit": pdef.unit,
            "precision": pdef.precision,
            "default": pdef.default,
            "temporal": pdef.temporal,
        }

    @staticmethod
    def _state_machine_descriptor(state_machine: dict | None) -> dict | None:
        if state_machine is None:
            return None
        return {
            "property": state_machine["property"],
            "initial": state_machine["initial"],
            "states": list(state_machine["states"]),
            "transitions": {
                state: list(targets)
                for state, targets in state_machine["transitions"].items()
            },
        }

    def _validate_property_map(
        self, otype: ObjectType, properties: dict, *, creating: bool
    ) -> None:
        if creating:
            for name, pdef in otype.properties.items():
                if pdef.required and properties.get(name) is None:
                    raise SchemaViolation(
                        f"{otype.name}.{name} is required"
                    )
        for name, value in properties.items():
            pdef = otype.properties.get(name)
            if pdef is None:
                raise SchemaViolation(f"{otype.name} has no property {name!r}")
            self._validate_property_value(otype, name, pdef, value)

    def _validate_property_value(self, otype: ObjectType, name: str, pdef, value) -> None:
        if not pdef.type.check(value):
            raise SchemaViolation(
                f"property {otype.name}.{name} expects "
                f"{pdef.type.kind}, got {type(value).__name__}"
            )
        if pdef.enum is not None and value not in pdef.enum:
            raise SchemaViolation(
                f"property {otype.name}.{name} must be one of "
                f"{list(pdef.enum)}, got {value!r}"
            )
        if pdef.minimum is not None:
            if not self._comparable(value, pdef.minimum) or value < pdef.minimum:
                raise SchemaViolation(
                    f"property {otype.name}.{name} must be >= {pdef.minimum}, "
                    f"got {value!r}"
                )
        if pdef.maximum is not None:
            if not self._comparable(value, pdef.maximum) or value > pdef.maximum:
                raise SchemaViolation(
                    f"property {otype.name}.{name} must be <= {pdef.maximum}, "
                    f"got {value!r}"
                )
        if pdef.precision is not None:
            self._check_precision(otype, name, pdef, value)
        if (
            pdef.unit is not None
            and isinstance(value, dict)
            and "unit" in value
            and value.get("unit") != pdef.unit
        ):
            raise SchemaViolation(
                f"property {otype.name}.{name} is measured in {pdef.unit!r}, "
                f"got {value.get('unit')!r}"
            )

    @staticmethod
    def _comparable(left: object, right: object) -> bool:
        try:
            left < right  # noqa: B015 - comparison probe only
        except TypeError:
            return False
        return True

    def _check_precision(self, otype: ObjectType, name: str, pdef, value) -> None:
        kind = pdef.type.kind
        if kind in ("number", "integer"):
            if isinstance(value, int) and not isinstance(value, bool):
                decimals = 0
            else:
                try:
                    exponent = Decimal(str(value)).as_tuple().exponent
                except (InvalidOperation, ValueError):
                    raise SchemaViolation(
                        f"property {otype.name}.{name}: cannot check precision "
                        f"of {value!r}"
                    ) from None
                decimals = max(0, -int(exponent)) if isinstance(exponent, int) else 0
            if decimals > pdef.precision:
                if kind == "number" and self._float_noise(
                    value, pdef.precision
                ):
                    return
                raise SchemaViolation(
                    f"property {otype.name}.{name} allows at most "
                    f"{pdef.precision} decimal places, got {value!r}"
                )
        elif kind == "string" and isinstance(value, str):
            if len(value) > pdef.precision:
                raise SchemaViolation(
                    f"property {otype.name}.{name} allows at most "
                    f"{pdef.precision} characters, got {len(value)}"
                )

    @staticmethod
    def _float_noise(value, precision: int) -> bool:
        """True when the extra decimals are only float representation noise
        (e.g. 0.1 + 0.2 = 0.30000000000000004 at precision 2)."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isclose(
            number, round(number, precision), rel_tol=0.0, abs_tol=1e-12
        )

    def _check_unique_conflicts(
        self,
        otype: ObjectType,
        properties: dict,
        ref: ObjectRef,
        overrides: dict[ObjectRef, dict] | None = None,
    ) -> None:
        """Check unique / identity keys against state plus a pending view.

        ``overrides`` maps refs that are not (yet) visible in the store - or
        whose pending properties differ from the store - to the properties
        they will have once this batch is applied.
        """
        overrides = overrides or {}
        seen: set[ObjectRef] = set()
        for other in self.store.find_objects(otype.name, {}):
            if other.ref == ref:
                continue
            seen.add(other.ref)
            other_properties = overrides.get(other.ref, other.properties)
            self._compare_keys(otype, properties, other.ref, other_properties)
        for other_ref, other_properties in overrides.items():
            if other_ref == ref or other_ref in seen:
                continue
            if other_ref.type != otype.name:
                continue
            self._compare_keys(otype, properties, other_ref, other_properties)

    def _compare_keys(
        self,
        otype: ObjectType,
        properties: dict,
        other_ref: ObjectRef,
        other_properties: dict,
    ) -> None:
        for name, pdef in otype.properties.items():
            if not pdef.unique:
                continue
            value = properties.get(name)
            if value is not None and other_properties.get(name) == value:
                raise SchemaViolation(
                    f"{otype.name}.{name} must be unique; "
                    f"{value!r} is already used by {other_ref}"
                )
        if otype.identity:
            mine = tuple(properties.get(p) for p in otype.identity)
            if any(value is None for value in mine):
                return
            theirs = tuple(other_properties.get(p) for p in otype.identity)
            if mine == theirs:
                raise SchemaViolation(
                    f"{otype.name} identity {mine} is already used by {other_ref}"
                )

    def _check_state_transitions(
        self, otype: ObjectType, current: dict, updates: dict
    ) -> None:
        state_machine = otype.state_machine
        if state_machine is None:
            return
        prop = state_machine["property"]
        if prop not in updates:
            return
        old = current.get(prop)
        new = updates[prop]
        if old == new:
            return
        states = state_machine["states"]
        if states and new not in states:
            raise SchemaViolation(
                f"{otype.name}.{prop}: {new!r} is not a declared state "
                f"{list(states)}"
            )
        if old is None:
            # no declared initial state: the first assignment may pick any
            # declared state (this is how a create without a default behaves)
            return
        allowed = state_machine["transitions"].get(old, ())
        if new not in allowed:
            raise SchemaViolation(
                f"invalid state transition on {otype.name}.{prop}: "
                f"{old!r} -> {new!r} (allowed from {old!r}: {list(allowed)})"
            )

    def _check_create_state(self, otype: ObjectType, properties: dict) -> None:
        state_machine = otype.state_machine
        if state_machine is None:
            return
        prop = state_machine["property"]
        value = properties.get(prop)
        if value is None:
            return
        initial = state_machine["initial"]
        states = state_machine["states"]
        if initial is not None and value != initial:
            raise SchemaViolation(
                f"{otype.name}.{prop} must start at {initial!r}, got {value!r}"
            )
        if states and value not in states:
            raise SchemaViolation(
                f"{otype.name}.{prop}: {value!r} is not a declared state "
                f"{list(states)}"
            )

    def _effective_create_properties(
        self, otype: ObjectType, provided: dict
    ) -> dict:
        properties = {
            name: pdef.default
            for name, pdef in otype.properties.items()
            if pdef.default is not None
        }
        state_machine = otype.state_machine
        if state_machine is not None and state_machine["initial"] is not None:
            properties.setdefault(state_machine["property"], state_machine["initial"])
        properties.update(provided)
        return properties

    # ---- object / relation write preparation ----

    def _prepare_upserts(
        self, objects: list[Object], *, source: str | None = None
    ) -> list[Object]:
        prepared: list[Object] = []
        seen: set[ObjectRef] = set()
        batch: dict[ObjectRef, dict] = {}
        for obj in objects:
            ref = obj.ref
            if ref in seen:
                raise SchemaViolation(f"duplicate object in write batch: {ref}")
            seen.add(ref)
            otype = self.schema.objects.get(ref.type)
            if otype is None:
                raise SchemaViolation(f"unknown object type: {ref.type!r}")
            existing = self.store.get_object(ref)
            if existing is None:
                properties = self._effective_create_properties(
                    otype, dict(obj.properties)
                )
                self._validate_property_map(otype, properties, creating=True)
                self._check_create_state(otype, properties)
                self._check_unique_conflicts(otype, properties, ref, batch)
                batch[ref] = properties
                prepared.append(
                    Object(
                        ref,
                        properties,
                        obj.version,
                        obj.updated_at,
                        obj.source if obj.source is not None else source,
                    )
                )
            else:
                updates = dict(obj.properties)
                self._validate_property_map(otype, updates, creating=False)
                merged = {**existing.properties, **updates}
                self._check_unique_conflicts(otype, merged, ref, batch)
                self._check_state_transitions(otype, existing.properties, updates)
                batch[ref] = merged
                prepared.append(Object(ref, updates))
        return prepared

    def _validate_relation_writes(
        self,
        relations: list[Relation],
        known_refs: set[ObjectRef],
        pending_out: dict[ObjectRef, set[ObjectRef]] | None = None,
        pending_in: dict[ObjectRef, set[ObjectRef]] | None = None,
    ) -> None:
        pending_out = {} if pending_out is None else pending_out
        pending_in = {} if pending_in is None else pending_in
        for rel in relations:
            rdef = self._relation_def(rel.relation)
            if rel.source.type != rdef.source:
                raise SchemaViolation(
                    f"relation {rdef.name} source must be {rdef.source}, "
                    f"got {rel.source.type}"
                )
            if rel.target.type != rdef.target:
                raise SchemaViolation(
                    f"relation {rdef.name} target must be {rdef.target}, "
                    f"got {rel.target.type}"
                )
            for ref in (rel.source, rel.target):
                if self.store.get_object(ref) is None and ref not in known_refs:
                    raise ObjectNotFound(f"object not found: {ref}")
            self._check_cardinality(
                rdef, rel.source, rel.target, pending_out, pending_in
            )
            pending_out.setdefault(rel.source, set()).add(rel.target)
            pending_in.setdefault(rel.target, set()).add(rel.source)

    def _check_cardinality(
        self,
        rdef,
        source: ObjectRef,
        target: ObjectRef,
        extra_out: dict[ObjectRef, set[ObjectRef]] | None = None,
        extra_in: dict[ObjectRef, set[ObjectRef]] | None = None,
    ) -> None:
        cardinality = rdef.cardinality
        if not cardinality:
            return
        out_targets = set(self.store.related(source, rdef.name, "out"))
        in_sources = set(self.store.related(target, rdef.name, "in"))
        out_targets |= set((extra_out or {}).get(source, ()))
        in_sources |= set((extra_in or {}).get(target, ()))
        if cardinality == "one-to-one":
            others = out_targets - {target}
            if others:
                raise SchemaViolation(
                    f"relation {rdef.name} is one-to-one: {source} already "
                    f"links {sorted(map(str, others))}"
                )
            others = in_sources - {source}
            if others:
                raise SchemaViolation(
                    f"relation {rdef.name} is one-to-one: {target} is already "
                    f"linked from {sorted(map(str, others))}"
                )
        elif cardinality == "one-to-many":
            # ERD reading (source -> target): one source, many targets; each
            # target is reached through at most one incoming edge.
            others = in_sources - {source}
            if others:
                raise SchemaViolation(
                    f"relation {rdef.name} is one-to-many: target end {target} "
                    f"is already linked from {sorted(map(str, others))} (a "
                    f"target has at most one incoming edge)"
                )
        elif cardinality == "many-to-one":
            # ERD reading (source -> target): many sources, one target; each
            # source has at most one outgoing edge.
            others = out_targets - {target}
            if others:
                raise SchemaViolation(
                    f"relation {rdef.name} is many-to-one: source end {source} "
                    f"already links {sorted(map(str, others))} (a source has "
                    f"at most one outgoing edge)"
                )

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    def _object_type(self, name: str) -> ObjectType:
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
