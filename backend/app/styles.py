"""Prompt styles: reusable positive/negative snippets, private per profile.

Distinct from presets (which hold every control value). A style is applied to the
bound prompt controls at submission; the effective, already-styled text is what
the generation records, so history shows exactly what was sent. Retries copy that
snapshot and never re-apply styles.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, StringConstraints, model_validator

from .auth.routes import CurrentPrincipal, Mutation
from .contracts import ExactInt, Id, Model, Revisioned
from .storage.db import Database, in_thread
from .storage.repository import LimitExceeded, RevisionConflict, new_id, now_ms

MAX_STYLES = 200
PLACEHOLDER = "{prompt}"

StyleName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
Snippet = Annotated[str, StringConstraints(max_length=4000)]


class SaveStyle(Model):
    name: StyleName
    positive: Snippet = ""
    negative: Snippet = ""


class UpdateStyle(Model):
    name: StyleName | None = None
    positive: Snippet | None = None
    negative: Snippet | None = None
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _something_to_change(self) -> UpdateStyle:
        if self.name is None and self.positive is None and self.negative is None:
            raise ValueError("send a name, positive text, negative text, or several")
        return self


class Style(Revisioned):
    id: Id
    name: str
    positive: str
    negative: str
    created_ms: ExactInt
    updated_ms: ExactInt


def apply_styles(
    styles: list[dict[str, Any]], controls: list[Any], edits: dict[str, Any]
) -> None:
    """Rewrite the traced positive/negative prompt controls in ``edits`` in place.

    Ambiguous or unconnected prompt controls are left alone: a style never guesses
    which side it belongs to. ``{prompt}`` in a positive snippet marks where the
    user's text goes; without it the snippet is appended.
    """
    if not styles:
        return
    for control in controls:
        if control.group != "prompts" or control.logical_type != "string":
            continue
        reason = control.inference_reason
        if "conditioning_traced:positive" in reason:
            side = "positive"
        elif "conditioning_traced:negative" in reason:
            side = "negative"
        else:
            continue
        current = edits.get(control.binding_id, control.value)
        text = current if isinstance(current, str) else ""
        for style in styles:
            snippet = style[side]
            if not snippet:
                continue
            if side == "positive" and PLACEHOLDER in snippet:
                text = snippet.replace(PLACEHOLDER, text)
            else:
                text = f"{text}, {snippet}" if text.strip() else snippet
        edits[control.binding_id] = text


class StyleStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, owner_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM prompt_styles WHERE owner_id = ? ORDER BY name COLLATE NOCASE, id",
            (owner_id,),
        )
        return [dict(row) for row in rows]

    def get_many(self, owner_id: str, ids: list[str]) -> list[dict[str, Any]] | None:
        """The owner's styles in ``ids`` order, or None if any is not theirs."""
        found = {row["id"]: row for row in self.list(owner_id)}
        if any(style_id not in found for style_id in ids):
            return None
        return [found[style_id] for style_id in ids]

    def create(self, owner_id: str, body: SaveStyle) -> dict[str, Any]:
        style_id, stamp = new_id(), now_ms()
        with self.db.write() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM prompt_styles WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()["n"]
            if count >= MAX_STYLES:
                raise LimitExceeded(
                    f"A profile holds at most {MAX_STYLES} styles. Delete one first."
                )
            conn.execute(
                "INSERT INTO prompt_styles (id, owner_id, name, positive, negative,"
                " revision, created_ms, updated_ms) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    style_id,
                    owner_id,
                    body.name,
                    body.positive,
                    body.negative,
                    stamp,
                    stamp,
                ),
            )
        return self._one(owner_id, style_id)

    def _one(self, owner_id: str, style_id: str) -> dict[str, Any]:
        rows = self.get_many(owner_id, [style_id])
        assert rows is not None
        return rows[0]

    def update(
        self, owner_id: str, style_id: str, body: UpdateStyle
    ) -> dict[str, Any] | None:
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT revision FROM prompt_styles WHERE id = ? AND owner_id = ?",
                (style_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            if int(row["revision"]) != body.expected_revision:
                raise RevisionConflict(
                    f"This style is at revision {row['revision']}, not "
                    f"{body.expected_revision}. Reload the styles before saving again."
                )
            conn.execute(
                "UPDATE prompt_styles SET name = COALESCE(?, name),"
                " positive = COALESCE(?, positive), negative = COALESCE(?, negative),"
                " revision = revision + 1, updated_ms = ? WHERE id = ? AND owner_id = ?",
                (body.name, body.positive, body.negative, now_ms(), style_id, owner_id),
            )
        return self._one(owner_id, style_id)

    def delete(self, owner_id: str, style_id: str) -> bool:
        with self.db.write() as conn:
            return (
                conn.execute(
                    "DELETE FROM prompt_styles WHERE id = ? AND owner_id = ?",
                    (style_id, owner_id),
                ).rowcount
                > 0
            )


def _style(row: dict[str, Any]) -> Style:
    return Style(
        id=row["id"],
        name=row["name"],
        positive=row["positive"],
        negative=row["negative"],
        revision=int(row["revision"]),
        created_ms=str(row["created_ms"]),
        updated_ms=str(row["updated_ms"]),
    )


router = APIRouter(prefix="/api/styles")


@router.get("", response_model=list[Style])
async def list_styles(request: Request, principal: CurrentPrincipal) -> list[Style]:
    rows = await in_thread(request.app.state.styles.list, principal.owner_id)
    return [_style(row) for row in rows]


@router.post("", response_model=Style, status_code=201, dependencies=[Mutation])
async def create_style(
    request: Request, principal: CurrentPrincipal, body: SaveStyle
) -> Style:
    try:
        row = await in_thread(request.app.state.styles.create, principal.owner_id, body)
    except LimitExceeded as exc:
        raise HTTPException(409, str(exc)) from exc
    return _style(row)


@router.put("/{style_id}", response_model=Style, dependencies=[Mutation])
async def update_style(
    request: Request, style_id: str, principal: CurrentPrincipal, body: UpdateStyle
) -> Style:
    try:
        row = await in_thread(
            request.app.state.styles.update, principal.owner_id, style_id, body
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "Style was not found.")
    return _style(row)


@router.delete("/{style_id}", status_code=204, dependencies=[Mutation])
async def delete_style(
    request: Request, style_id: str, principal: CurrentPrincipal
) -> Response:
    deleted = await in_thread(
        request.app.state.styles.delete, principal.owner_id, style_id
    )
    if not deleted:
        raise HTTPException(404, "Style was not found.")
    return Response(status_code=204)
