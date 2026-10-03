"""Owner-scoped workflow import and control-schema assembly.

Thin by design: it sequences the mapping package over the existing owner-scoped
``Repository``. The imported graph is stored immutably as revision 1 and is
never edited in place -- controls are a presentation layer, and a submission
gets its own deep copy (app/mapping/submission.py).

A graph can be imported and stored while the catalog is unavailable; its
controls are then explicitly not validated and submission is refused
(docs/WORKFLOW_MAPPING.md "Catalog lifecycle").
"""

from __future__ import annotations

import json
from typing import Any

from ..catalog.contracts import CatalogSnapshot
from ..contracts import ControlSchema
from ..mapping import build_control_schema, parse_graph
from ..mapping.corrections import (
    ALLOWED_COMPONENTS,
    Correction,
    CorrectionError,
    Presentation,
    SaveCorrection,
    apply_corrections,
    effective_bounds,
    expected_signature,
    node_selector,
    structural_signature,
    workflow_selector,
)
from ..mapping.submission import apply_edits
from ..storage.repository import Repository, RevisionConflict
from .layout import LayoutDoc, SaveLayout, WorkflowLayout, section_items, stale_bindings


class WorkflowService:
    """Create one per process and share it; ``Repository`` calls block."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def import_workflow(
        self, owner_id: str, name: str, payload: bytes | str
    ) -> tuple[str, dict[str, Any]]:
        """Validate and store an API graph. Raises ``GraphImportError``.

        Returns ``(workflow_id, graph)``. The stored graph is exactly what was
        parsed: no link is repaired, no node dropped, no default invented.
        """
        graph = parse_graph(payload)
        workflow_id = self._repository.create_workflow(owner_id, name, graph)
        return workflow_id, graph

    def replace_graph(
        self,
        owner_id: str,
        workflow_id: str,
        payload: bytes | str,
        snapshot: CatalogSnapshot,
    ) -> tuple[dict[str, Any], int, list[str], list[str]] | None:
        """Store ``payload`` as the next graph revision; None if not owned.

        Raises ``GraphImportError`` (nothing written) or ``RevisionConflict``.
        Layout, corrections and presets are untouched: whatever no longer
        matches is reported stale by the existing readers. Returns
        ``(workflow row, new revision, added binding ids, removed binding ids)``
        where added/removed compare the control schemas, not the raw graphs.
        """
        base = self._base_schema(owner_id, workflow_id, snapshot)
        if base is None:
            return None
        _, old = base
        graph = parse_graph(payload)
        new = build_control_schema(
            graph,
            snapshot.nodes,
            owner_id=owner_id,
            workflow_id=workflow_id,
            revision=old.revision + 1,
            catalog_available=snapshot.freshness.state != "unavailable",
        )
        revision = self._repository.replace_workflow_graph(
            owner_id, workflow_id, graph, old.revision
        )
        if revision is None:  # pragma: no cover - deleted between the two reads
            return None
        before = {c.binding_id for c in old.controls}
        after = {c.binding_id for c in new.controls}
        return (
            self._repository.get_workflow(owner_id, workflow_id),
            revision,
            [c.binding_id for c in new.controls if c.binding_id not in before],
            [c.binding_id for c in old.controls if c.binding_id not in after],
        )

    def control_schema(
        self, owner_id: str, workflow_id: str, snapshot: CatalogSnapshot
    ) -> ControlSchema | None:
        """The presentation schema for a stored workflow, or None if not owned.

        The deterministic schema is built first and the profile's own saved
        corrections are replayed over it; nothing is read from another profile,
        because every repository call here is owner-scoped.
        """
        base = self._base_schema(owner_id, workflow_id, snapshot)
        if base is None:
            return None
        graph, schema = base
        overrides = self._repository.list_mapping_overrides(owner_id, workflow_id)
        return apply_corrections(
            schema, overrides, snapshot.nodes, signature=structural_signature(graph)
        )

    def replace_values(
        self,
        owner_id: str,
        workflow_id: str,
        edits: dict[str, Any],
        expected_revision: int,
        snapshot: CatalogSnapshot,
    ) -> tuple[dict[str, Any], int, list[str], list[str]] | None:
        """Decode edits into a new graph revision; None if not owned."""
        schema = self.control_schema(owner_id, workflow_id, snapshot)
        if schema is None:
            return None
        if schema.revision != expected_revision:
            raise RevisionConflict(
                f"This workflow is at graph revision {schema.revision}, not {expected_revision}. "
                "Reload it before replacing the graph."
            )
        graph = self._repository.get_workflow_graph(
            owner_id, workflow_id, schema.revision
        )
        if graph is None:  # pragma: no cover - written in the same transaction
            return None
        updated = apply_edits(graph, schema, edits)
        revision = self._repository.replace_workflow_graph(
            owner_id, workflow_id, updated, expected_revision
        )
        if revision is None:  # pragma: no cover - deleted between the two reads
            return None
        return self._repository.get_workflow(owner_id, workflow_id), revision, [], []

    # --- saved corrections ------------------------------------------------

    def save_correction(
        self,
        owner_id: str,
        workflow_id: str,
        snapshot: CatalogSnapshot,
        request: SaveCorrection,
    ) -> Correction:
        """Persist one correction against the control it actually binds to.

        The selector and the signature are derived here from the live schema,
        never taken from the request: a client cannot key a correction to a
        binding or a structure that does not exist.
        """
        if snapshot.freshness.state == "unavailable":
            # Controls derived without a catalog are explicitly not validated,
            # so a correction keyed to them would go stale the moment ComfyUI
            # answers again (docs/WORKFLOW_MAPPING.md "Catalog lifecycle").
            raise CorrectionError(
                409,
                "No node catalog is available, so a correction cannot be keyed to validated "
                "controls yet. Save it once ComfyUI has been reached.",
            )
        base = self._base_schema(owner_id, workflow_id, snapshot)
        if base is None:
            raise CorrectionError(404, "Workflow was not found.")
        graph, schema = base
        control = next(
            (c for c in schema.controls if c.binding_id == request.binding_id), None
        )
        if control is None:
            raise CorrectionError(
                404, f"No control {request.binding_id!r} exists in this workflow."
            )
        self._check_presentation(request, control)

        signature = expected_signature(
            request.scope, control, structural_signature(graph), snapshot.nodes
        )
        selector = (
            workflow_selector(control)
            if request.scope == "workflow"
            else node_selector(control)
        )
        try:
            revision = self._repository.save_mapping_override(
                owner_id,
                scope=request.scope,
                # A node template is profile-wide, so it carries no workflow.
                workflow_id=workflow_id if request.scope == "workflow" else None,
                selector=selector,
                schema_signature=signature,
                presentation=request.presentation.model_dump(exclude_none=True),
                expected_revision=request.expected_revision,
            )
        except RevisionConflict as exc:
            raise CorrectionError(409, str(exc)) from exc
        return Correction(
            scope=request.scope,
            selector=selector,
            schema_signature=signature,
            presentation=request.presentation,
            revision=revision,
        )

    def export_corrections(
        self, owner_id: str, workflow_id: str, snapshot: CatalogSnapshot
    ) -> list[Correction] | None:
        """Every correction that applies to this workflow, stale ones included."""
        base = self._base_schema(owner_id, workflow_id, snapshot)
        if base is None:
            return None
        graph, schema = base
        signature = structural_signature(graph)
        #: (scope, selector) -> the signature a correction must carry to apply.
        live = {
            (scope, selector(control)): expected_signature(
                scope, control, signature, snapshot.nodes
            )
            for control in schema.controls
            for scope, selector in (
                ("workflow", workflow_selector),
                ("node_class", node_selector),
            )
        }
        exported = []
        for row in self._repository.list_mapping_overrides(owner_id, workflow_id):
            key = (row["scope"], row["binding_selector"])
            exported.append(
                Correction(
                    scope=row["scope"],
                    selector=row["binding_selector"],
                    schema_signature=row["schema_signature"],
                    presentation=Presentation.model_validate(
                        json.loads(row["presentation_json"])
                    ),
                    revision=int(row["revision"]),
                    stale=live.get(key) != row["schema_signature"],
                )
            )
        return exported

    def reset_corrections(
        self,
        owner_id: str,
        workflow_id: str,
        scope: str | None = None,
        selector: str | None = None,
    ) -> int:
        """Drop saved corrections so the derived base schema returns unchanged.

        Default (no scope): this workflow's own corrections only -- a reset here
        never silently removes the profile's node templates. ``node_class``
        resets templates, which are profile-wide and carry no workflow.
        Every delete is owner-scoped, so it can only reach the caller's rows.
        """
        if scope == "node_class":
            return self._repository.delete_mapping_overrides(
                owner_id, scope=scope, selector=selector
            )
        return self._repository.delete_mapping_overrides(
            owner_id, workflow_id=workflow_id, scope=scope, selector=selector
        )

    # --- saved layouts ----------------------------------------------------

    def get_layout(
        self, owner_id: str, workflow_id: str, snapshot: CatalogSnapshot
    ) -> WorkflowLayout | None:
        """The saved layout (revision 0, null when none), or None if not owned."""
        base = self._base_schema(owner_id, workflow_id, snapshot)
        if base is None:
            return None
        graph, schema = base
        row = self._repository.get_layout(owner_id, workflow_id)
        layout = (
            None if row is None else LayoutDoc.model_validate_json(row["layout_json"])
        )
        return WorkflowLayout(
            workflow_id=workflow_id,
            revision=0 if row is None else int(row["revision"]),
            schema_signature=structural_signature(graph),
            layout=layout,
            stale_bindings=stale_bindings(
                layout, {c.binding_id for c in schema.controls}
            ),
        )

    def save_layout(
        self,
        owner_id: str,
        workflow_id: str,
        snapshot: CatalogSnapshot,
        request: SaveLayout,
    ) -> WorkflowLayout | None:
        """Persist the layout optimistically; raises ``RevisionConflict``.

        A layout is pure arrangement, so there is no catalog-availability check
        (unlike corrections): unknown binding ids are kept and reported stale.
        """
        base = self._base_schema(owner_id, workflow_id, snapshot)
        if base is None:
            return None
        graph, schema = base
        controls = {c.binding_id: c for c in schema.controls}
        saved = self._repository.get_layout(owner_id, workflow_id)
        previous = (
            LayoutDoc.model_validate_json(saved["layout_json"]) if saved else None
        )
        previous_pairs = (
            {
                (item.width, item.height)
                for section in previous.sections
                for item in section_items(section)
                if item.kind == "aspect_ratio"
            }
            if previous
            else set()
        )
        for section in request.layout.sections:
            for item in section_items(section):
                if item.kind == "aspect_ratio":
                    for binding in (item.width, item.height):
                        control = controls.get(binding)
                        if (
                            control is None
                            and (item.width, item.height) not in previous_pairs
                        ) or (control is not None and control.logical_type != "int"):
                            raise CorrectionError(
                                422, "Aspect-ratio bindings must be integer controls."
                            )
        self._repository.save_layout(
            owner_id,
            workflow_id,
            layout=request.layout.model_dump(),
            schema_signature=structural_signature(graph),
            expected_revision=request.expected_revision,
        )
        return self.get_layout(owner_id, workflow_id, snapshot)

    def delete_layout(
        self, owner_id: str, workflow_id: str, expected_revision: int | None = None
    ) -> bool:
        """Revert to the automatic layout. False only when the workflow is not owned.

        With ``expected_revision`` a mismatch raises ``RevisionConflict``.
        """
        if self._repository.get_workflow(owner_id, workflow_id) is None:
            return False
        self._repository.delete_layout(owner_id, workflow_id, expected_revision)
        return True

    # --- internals --------------------------------------------------------

    def _base_schema(
        self, owner_id: str, workflow_id: str, snapshot: CatalogSnapshot
    ) -> tuple[dict[str, Any], ControlSchema] | None:
        workflow = self._repository.get_workflow(owner_id, workflow_id)
        if workflow is None:
            return None
        revision = int(workflow["current_revision"])
        graph = self._repository.get_workflow_graph(owner_id, workflow_id, revision)
        if graph is None:  # pragma: no cover - written in the same transaction
            return None
        return graph, build_control_schema(
            graph,
            snapshot.nodes,
            owner_id=owner_id,
            workflow_id=workflow_id,
            revision=revision,
            catalog_available=snapshot.freshness.state != "unavailable",
        )

    @staticmethod
    def _check_presentation(request: SaveCorrection, control: Any) -> None:
        """A widget choice must suit the control; a template stores no context.

        ``Presentation`` already forbids unknown fields, so values, prompts,
        filenames and seeds cannot reach a saved template at all.
        """
        component = request.presentation.component
        allowed = ALLOWED_COMPONENTS.get(control.logical_type, frozenset())
        if component is not None and component not in allowed:
            raise CorrectionError(
                422,
                f"A {control.logical_type} control cannot be shown as {component!r}. "
                f"Allowed here: {', '.join(sorted(allowed))}.",
            )

        default = request.presentation.display_default
        if default is None:
            return
        bounds = effective_bounds(control, request.presentation)
        if bounds is None:
            raise CorrectionError(
                422,
                f"{control.class_type}.{control.input_name} has no adjustable numeric range, "
                "so a default cannot be saved for it.",
            )
        low, high = bounds
        if (low is not None and default < low) or (high is not None and default > high):
            raise CorrectionError(
                422,
                f"The default {default!r} is outside the resolved range "
                f"[{low if low is not None else '-inf'}, {high if high is not None else 'inf'}] "
                f"for {control.class_type}.{control.input_name}.",
            )
