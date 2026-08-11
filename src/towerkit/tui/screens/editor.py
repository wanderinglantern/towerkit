"""The editor: Structure | Detail | Preview panes over a diagnostics bar.

Validation runs on every change and never blocks. Money fields accept
shorthand; shares are percentages stored as bps with over-signing blocked at
the input; saves are canonical JSON and never silent about errors.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import (
    Horizontal,
    HorizontalGroup,
    Vertical,
    VerticalGroup,
    VerticalScroll,
)
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    Tree,
)

from ...model import (
    Line,
    Participant,
    Placement,
    Program,
    Retention,
    RetentionType,
    Sublimit,
)
from ...money import (
    BPS_SCALE,
    format_money,
    format_share,
    premium_share,
)
from ...theme import load_theme
from ...validate import Diagnostic
from ..session import EditSession
from ..widgets.inputs import (
    CarrierSuggester,
    MoneyInput,
    ShareValidator,
    known_carriers,
    parse_share_pct,
)
from ..widgets.modals import ConfirmModal, PromptModal
from ..widgets.preview import TowerPreview

NodeRef = tuple[str, Any]


class DiagItem(ListItem):
    def __init__(self, label: Label, diag_ref: NodeRef) -> None:
        super().__init__(label)
        self.diag_ref: NodeRef = diag_ref


class EditorScreen(Screen):
    BINDINGS = [
        ("ctrl+s", "save", "Save"),
        ("u", "undo", "Undo"),
        ("ctrl+r", "redo", "Redo"),
        ("r", "render", "Render"),
        ("a", "add_node", "Add"),
        ("delete", "remove_node", "Remove"),
        ("escape", "back", "Back"),
    ]

    CSS = """
    #panes { height: 1fr; }
    #structure { width: 30; border-right: solid $panel; }
    #detail { width: 46; border-right: solid $panel; padding: 0 1; }
    #preview-pane { width: 1fr; }
    #diagnostics { height: 6; border-top: solid $panel; }
    .field-label { color: $text-muted; margin-top: 1; }
    .row-total { color: $text-muted; }
    Input.-invalid { border: tall $error; }
    #detail Input { width: 1fr; }
    #detail HorizontalGroup > Input { width: 1fr; }
    #detail Button { margin-top: 1; }
    #applies-row { height: auto; }
    .applies-line { height: auto; }
    .applies-line Checkbox { width: 1fr; }
    .participant-row { height: 3; }
    .participant-row Input { width: 1fr; }
    .participant-row Static { width: 12; content-align: right middle; height: 3; }
    .participant-row Button { min-width: 5; margin-top: 0; }
    """

    def __init__(self, session: EditSession) -> None:
        super().__init__()
        self.session = session
        self.selected: NodeRef = ("program", None)
        self._detail_lock = asyncio.Lock()
        self.tower_theme = load_theme(None)
        self._carriers = known_carriers(
            Path("programs"), Path("themes"), session.program.carriers()
        )

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="panes"):
            yield Tree("Program", id="structure")
            yield VerticalScroll(id="detail")
            with Vertical(id="preview-pane"):
                yield TowerPreview(self.tower_theme, id="preview")
        yield ListView(id="diagnostics")
        yield Footer()

    async def on_mount(self) -> None:
        self.refresh_all()
        await self._rebuild_detail()
        self.query_one("#structure", Tree).focus()

    # -- refresh machinery ----------------------------------------------------

    def refresh_all(self) -> None:
        self._refresh_tree()
        self._refresh_preview()
        self._refresh_diagnostics()
        self._refresh_title()

    def _refresh_title(self) -> None:
        program = self.session.program
        star = "*" if self.session.dirty else ""
        name = self.session.path.name if self.session.path else "unsaved"
        self.app.sub_title = f"{program.insured} — {name}{star}"

    def _refresh_preview(self) -> None:
        self.query_one("#preview", TowerPreview).show_program(self.session.program)

    def _marker(self, diags: list[Diagnostic]) -> str:
        if any(d.severity == "error" for d in diags):
            return " ✗"
        if diags:
            return " ⚠"
        return ""

    def _refresh_tree(self) -> None:
        tree = self.query_one("#structure", Tree)
        program = self.session.program
        diags = self.session.diagnostics()
        tree.clear()
        tree.root.data = ("program", None)
        tree.root.label = f"{program.insured}{self._marker(diags.for_ref(('program', None)))}"

        lines = tree.root.add(f"Lines ({len(program.lines)})", data=("lines-group", None))
        for line in program.lines:
            marker = self._marker(diags.for_ref(("line", line.id)))
            lines.add_leaf(f"{line.name}{marker}", data=("line", line.id))

        layers = tree.root.add(f"Layers ({len(program.layers)})", data=("layers-group", None))
        for layer in sorted(program.layers, key=lambda ly: (ly.attach, ly.id)):
            marker = self._marker(diags.for_ref(("layer", layer.id)))
            spans = "/".join(layer.applies_to)
            layers.add_leaf(f"{layer.name} [{spans}]{marker}", data=("layer", layer.id))

        rets = tree.root.add(
            f"Retentions ({len(program.retentions)})", data=("retentions-group", None)
        )
        for idx, retention in enumerate(program.retentions):
            marker = self._marker(diags.for_ref(("retention", idx)))
            label = f"{'/'.join(retention.applies_to)} {retention.type.value}{marker}"
            rets.add_leaf(label, data=("retention", idx))

        subs = tree.root.add(
            f"Sublimits ({len(program.sublimits)})", data=("sublimits-group", None)
        )
        for idx, sublimit in enumerate(program.sublimits):
            marker = self._marker(diags.for_ref(("sublimit", idx)))
            subs.add_leaf(f"{sublimit.name}{marker}", data=("sublimit", idx))

        tree.root.expand_all()

    def _refresh_diagnostics(self) -> None:
        panel = self.query_one("#diagnostics", ListView)
        panel.clear()
        for diag in self.session.diagnostics().items:
            panel.append(DiagItem(Label(str(diag)), diag.ref))

    # -- selection ------------------------------------------------------------

    async def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.selected = event.node.data
            await self._rebuild_detail()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        ref = getattr(event.item, "diag_ref", None)
        if ref is None:
            return
        kind = ref[0]
        self.selected = ref if kind != "program" else ("program", None)
        self._select_tree_node(ref)
        await self._rebuild_detail()

    def _select_tree_node(self, ref: NodeRef) -> None:
        tree = self.query_one("#structure", Tree)
        for node in self._walk(tree.root):
            if node.data == ref:
                tree.select_node(node)
                tree.scroll_to_node(node)
                return

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    # -- detail forms ----------------------------------------------------------

    async def _rebuild_detail(self) -> None:
        async with self._detail_lock:
            await self._rebuild_detail_locked()

    async def _rebuild_detail_locked(self) -> None:
        detail = self.query_one("#detail", VerticalScroll)
        await detail.remove_children()
        kind, key = self.selected
        builders: dict[str, Callable[[Any], list[Any]]] = {
            "program": self._form_program,
            "lines-group": self._form_hint,
            "layers-group": self._form_hint,
            "retentions-group": self._form_hint,
            "sublimits-group": self._form_hint,
            "line": self._form_line,
            "layer": self._form_layer,
            "retention": self._form_retention,
            "sublimit": self._form_sublimit,
        }
        builder = builders.get(kind, self._form_hint)
        await detail.mount_all(builder(key))

    def _form_hint(self, _key: Any) -> list:
        return [
            Static(
                "Select a node to edit it.\n\n"
                "a — add an item to the selected group\n"
                "delete — remove the selected item\n"
                "r — render · ctrl+s — save · u/ctrl+r — undo/redo"
            )
        ]

    def _form_program(self, _key: Any) -> list:
        program = self.session.program
        return [
            Label("Insured", classes="field-label"),
            Input(value=program.insured, id="f-insured"),
            Label("Program", classes="field-label"),
            Input(value=program.program, id="f-program"),
            Label("Placement", classes="field-label"),
            Select(
                [(p.value, p.value) for p in Placement],
                value=program.placement.value,
                id="f-placement",
                allow_blank=False,
            ),
            Label("Period start / end (ISO)", classes="field-label"),
            HorizontalGroup(
                Input(value=program.period.start.isoformat(), id="f-period-start"),
                Input(value=program.period.end.isoformat(), id="f-period-end"),
            ),
            Label(
                f"Total limit {format_money(program.total_limit())} · "
                f"premium {format_money(program.total_premium())}",
                classes="row-total",
            ),
        ]

    def _form_line(self, line_id: str) -> list:
        line = self._line(line_id)
        if line is None:
            return self._form_hint(None)
        return [
            Label("Id", classes="field-label"),
            Input(value=line.id, id="f-line-id"),
            Label("Name", classes="field-label"),
            Input(value=line.name, id="f-line-name"),
            Label("Column label", classes="field-label"),
            Input(value=line.abbr or "", id="f-line-abbr", placeholder=line.id.upper()),
        ]


    def _applies_selector(self, selected: list[str]) -> VerticalGroup:
        """Checkbox grid for appliesTo. Wrapped into rows — a single
        Horizontal row clips silently once there are more than ~3 lines."""
        boxes = [
            Checkbox(
                line.id,
                value=line.id in selected,
                id=f"applies-{line.id}",
            )
            for line in self.session.program.lines
        ]
        per_row = 3
        rows = [
            HorizontalGroup(*boxes[i : i + per_row], classes="applies-line")
            for i in range(0, len(boxes), per_row)
        ]
        return VerticalGroup(*rows, id="applies-row")

    def _form_layer(self, layer_id: str) -> list:
        layer = self._layer(layer_id)
        if layer is None:
            return self._form_hint(None)
        widgets: list = [
            Label(f"Layer: {layer.name}", classes="field-label"),
            Label("Name", classes="field-label"),
            Input(value=layer.name, id="f-layer-name"),
            Label("Applies to", classes="field-label"),
        ]
        widgets.append(self._applies_selector(layer.applies_to))
        widgets += [
            Label("Attach", classes="field-label"),
            MoneyInput(layer.attach, id="f-layer-attach"),
            Label("Limit", classes="field-label"),
            MoneyInput(layer.limit if layer.limit > 0 else None, id="f-layer-limit"),
            Label("Premium", classes="field-label"),
            MoneyInput(layer.premium, id="f-layer-premium"),
            Label("— Participants —", classes="field-label"),
        ]
        widgets += self._participant_rows(layer)
        widgets += [
            Static(self._signed_summary(layer), id="signed-summary", classes="row-total"),
            Button("Add participant", id="add-participant"),
        ]
        return widgets

    def _participant_rows(self, layer) -> list:
        rows = []
        suggester = CarrierSuggester(self._carriers)
        for idx, participant in enumerate(layer.participants):
            prem = (
                format_money(premium_share(layer.premium, participant.share_bps))
                if layer.premium is not None
                else "—"
            )
            rows.append(
                HorizontalGroup(
                    Input(
                        value=participant.carrier,
                        suggester=suggester,
                        id=f"p-carrier-{idx}",
                    ),
                    Input(
                        value=f"{participant.share_bps / 100:g}",
                        validators=[ShareValidator()],
                        id=f"p-share-{idx}",
                    ),
                    Static(prem, id=f"p-prem-{idx}"),
                    Button("✕", id=f"p-del-{idx}"),
                    classes="participant-row",
                )
            )
        return rows

    def _signed_summary(self, layer) -> str:
        signed = layer.signed_bps
        open_bps = max(0, BPS_SCALE - signed)
        open_dollars = layer.limit * open_bps // BPS_SCALE if layer.limit > 0 else 0
        if signed >= BPS_SCALE:
            return f"{format_share(signed)} signed"
        return (
            f"{format_share(signed)} signed · {format_share(open_bps)} open · "
            f"{format_money(open_dollars)}"
        )

    def _form_retention(self, index: int) -> list:
        program = self.session.program
        if index >= len(program.retentions):
            return self._form_hint(None)
        retention = program.retentions[index]
        return [
            Label("Applies to", classes="field-label"),
            self._applies_selector(retention.applies_to),
            Label("Type", classes="field-label"),
            Select(
                [(t.value, t.value) for t in RetentionType],
                value=retention.type.value,
                id="f-ret-type",
                allow_blank=False,
            ),
            Label("Amount (per occurrence)", classes="field-label"),
            MoneyInput(retention.amount, id="f-ret-amount"),
            Label("Aggregate (optional)", classes="field-label"),
            MoneyInput(retention.aggregate, id="f-ret-aggregate"),
            Label("Captive vehicle", classes="field-label"),
            Input(value=retention.vehicle or "", id="f-ret-vehicle"),
        ]

    def _form_sublimit(self, index: int) -> list:
        program = self.session.program
        if index >= len(program.sublimits):
            return self._form_hint(None)
        sublimit = program.sublimits[index]
        return [
            Label("Name", classes="field-label"),
            Input(value=sublimit.name, id="f-sub-name"),
            Label("Amount", classes="field-label"),
            MoneyInput(sublimit.amount, id="f-sub-amount"),
            Label("Applies to", classes="field-label"),
            self._applies_selector(sublimit.applies_to),
        ]

    # -- model lookups ---------------------------------------------------------

    def _line(self, line_id: str) -> Line | None:
        return next((ln for ln in self.session.program.lines if ln.id == line_id), None)

    def _layer(self, layer_id: str):
        return next(
            (ly for ly in self.session.program.layers if ly.id == layer_id), None
        )

    # -- field commit ----------------------------------------------------------

    @on(Input.Submitted)
    def _input_submitted(self, event: Input.Submitted) -> None:
        self._commit_input(event.input)

    @on(Input.Blurred)
    def _input_blurred(self, event: Input.Blurred) -> None:
        self._commit_input(event.input)

    def _commit_input(self, widget: Input) -> None:
        wid = widget.id or ""
        kind, key = self.selected
        handler = _FIELD_HANDLERS.get(wid)
        if handler is not None:
            handler(self, widget)
        elif wid.startswith("p-carrier-") or wid.startswith("p-share-"):
            self._commit_participant(widget, wid)

    def _mutate_and_refresh(self, fn) -> None:
        self.session.mutate(fn)
        self.refresh_all()

    def _commit_program_field(self, widget: Input) -> None:
        wid = widget.id
        value = widget.value.strip()
        if wid == "f-insured" and value:
            self._mutate_and_refresh(lambda p: setattr(p, "insured", value))
        elif wid == "f-program" and value:
            self._mutate_and_refresh(lambda p: setattr(p, "program", value))
        elif wid in ("f-period-start", "f-period-end"):
            from datetime import date

            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                self.notify("period dates are ISO: 2026-01-01", severity="error")
                return
            attr = "start" if wid == "f-period-start" else "end"

            def set_period(p: Program) -> None:
                p.period = p.period.model_copy(update={attr: parsed})

            self._mutate_and_refresh(set_period)

    def _commit_line_field(self, widget: Input) -> None:
        kind, key = self.selected
        line = self._line(key)
        if line is None:
            return
        value = widget.value.strip()
        if widget.id == "f-line-name" and value:
            self._mutate_and_refresh(lambda p: setattr(line, "name", value))
        elif widget.id == "f-line-abbr":
            self._mutate_and_refresh(lambda p: setattr(line, "abbr", value or None))
        elif widget.id == "f-line-id" and value and value != line.id:
            old = line.id

            def rename(p: Program) -> None:
                line.id = value
                for layer in p.layers:
                    layer.applies_to = [value if lid == old else lid for lid in layer.applies_to]
                for r in p.retentions:
                    r.applies_to = [value if lid == old else lid for lid in r.applies_to]
                for s in p.sublimits:
                    s.applies_to = [value if lid == old else lid for lid in s.applies_to]

            self.selected = ("line", value)
            self._mutate_and_refresh(rename)

    def _commit_layer_field(self, widget: Input) -> None:
        kind, key = self.selected
        layer = self._layer(key)
        if layer is None:
            return
        wid = widget.id
        if wid == "f-layer-name":
            value = widget.value.strip()
            if value:
                self._mutate_and_refresh(lambda p: setattr(layer, "name", value))
            return
        if not isinstance(widget, MoneyInput):
            return
        amount = widget.amount
        if amount is None and widget.value.strip():
            self.notify(f"can't parse {widget.value!r} as money", severity="error")
            return
        if wid == "f-layer-attach" and amount is not None:
            self._mutate_and_refresh(lambda p: setattr(layer, "attach", amount))
        elif wid == "f-layer-limit" and amount is not None:
            self._mutate_and_refresh(lambda p: setattr(layer, "limit", amount))
        elif wid == "f-layer-premium":
            self._mutate_and_refresh(lambda p: setattr(layer, "premium", amount))
            self._refresh_participant_premiums(layer)

    def _refresh_participant_premiums(self, layer) -> None:
        for idx, participant in enumerate(layer.participants):
            try:
                static = self.query_one(f"#p-prem-{idx}", Static)
            except Exception:
                continue
            prem = (
                format_money(premium_share(layer.premium, participant.share_bps))
                if layer.premium is not None
                else "—"
            )
            static.update(prem)

    def _commit_retention_field(self, widget: Input) -> None:
        kind, index = self.selected
        program = self.session.program
        if index >= len(program.retentions):
            return
        retention = program.retentions[index]
        wid = widget.id
        if wid == "f-ret-vehicle":
            value = widget.value.strip() or None
            self._mutate_and_refresh(lambda p: setattr(retention, "vehicle", value))
            return
        if not isinstance(widget, MoneyInput):
            return
        amount = widget.amount
        if amount is None and widget.value.strip():
            self.notify(f"can't parse {widget.value!r} as money", severity="error")
            return
        if wid == "f-ret-amount" and amount is not None:
            self._mutate_and_refresh(lambda p: setattr(retention, "amount", amount))
        elif wid == "f-ret-aggregate":
            self._mutate_and_refresh(lambda p: setattr(retention, "aggregate", amount))

    def _commit_sublimit_field(self, widget: Input) -> None:
        kind, index = self.selected
        program = self.session.program
        if index >= len(program.sublimits):
            return
        sublimit = program.sublimits[index]
        if widget.id == "f-sub-name":
            value = widget.value.strip()
            if value:
                self._mutate_and_refresh(lambda p: setattr(sublimit, "name", value))
        elif widget.id == "f-sub-amount" and isinstance(widget, MoneyInput):
            amount = widget.amount
            if amount is not None:
                self._mutate_and_refresh(lambda p: setattr(sublimit, "amount", amount))

    def _commit_participant(self, widget: Input, wid: str) -> None:
        kind, key = self.selected
        layer = self._layer(key)
        if layer is None:
            return
        idx = int(wid.rsplit("-", 1)[1])
        if idx >= len(layer.participants):
            return
        participant = layer.participants[idx]
        if wid.startswith("p-carrier-"):
            value = widget.value.strip()
            if value and value != participant.carrier:
                self._mutate_and_refresh(lambda p: setattr(participant, "carrier", value))
                if value not in self._carriers:
                    self._carriers.append(value)
        else:
            bps = parse_share_pct(widget.value)
            if bps is None:
                if widget.value.strip():
                    self.notify("share is a percentage: 35 or 33.33", severity="error")
                return
            others = sum(
                p.share_bps for i, p in enumerate(layer.participants) if i != idx
            )
            if others + bps > BPS_SCALE:
                # block over-signing at the input, don't just flag it after
                widget.value = f"{participant.share_bps / 100:g}"
                self.notify(
                    f"over-signed: {format_share(others + bps)} > 100%", severity="error"
                )
                return
            self._mutate_and_refresh(lambda p: setattr(participant, "share_bps", bps))
            self._refresh_participant_premiums(layer)
            try:
                self.query_one("#signed-summary", Static).update(self._signed_summary(layer))
            except Exception:
                pass

    # -- checkbox / select commits --------------------------------------------

    @on(Checkbox.Changed)
    def _checkbox_changed(self, event: Checkbox.Changed) -> None:
        wid = event.checkbox.id or ""
        if not wid.startswith("applies-"):
            return
        line_id = wid.removeprefix("applies-")
        kind, key = self.selected
        target = None
        if kind == "layer":
            target = self._layer(key)
        elif kind == "retention" and key < len(self.session.program.retentions):
            target = self.session.program.retentions[key]
        elif kind == "sublimit" and key < len(self.session.program.sublimits):
            target = self.session.program.sublimits[key]
        if target is None:
            return
        order = self.session.program.line_ids()
        current = set(target.applies_to)
        if event.value:
            current.add(line_id)
        else:
            current.discard(line_id)
        if not current:
            event.checkbox.value = True  # an empty appliesTo is meaningless
            self.notify("a layer must apply to at least one line", severity="warning")
            return
        new_order = [lid for lid in order if lid in current]
        self._mutate_and_refresh(lambda p: setattr(target, "applies_to", new_order))

    @on(Select.Changed)
    def _select_changed(self, event: Select.Changed) -> None:
        wid = event.select.id or ""
        value = event.value
        if value is Select.BLANK:
            return
        if wid == "f-placement":
            self._mutate_and_refresh(
                lambda p: setattr(p, "placement", Placement(str(value)))
            )
        elif wid == "f-ret-type":
            kind, index = self.selected
            if kind == "retention" and index < len(self.session.program.retentions):
                retention = self.session.program.retentions[index]
                self._mutate_and_refresh(
                    lambda p: setattr(retention, "type", RetentionType(str(value)))
                )

    # -- buttons ---------------------------------------------------------------

    @on(Button.Pressed)
    async def _button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id or ""
        kind, key = self.selected
        if wid == "add-participant" and kind == "layer":
            layer = self._layer(key)
            if layer is None:
                return
            open_bps = max(0, BPS_SCALE - layer.signed_bps)
            self._mutate_and_refresh(
                lambda p: layer.participants.append(
                    Participant(carrier="New Carrier", share_bps=open_bps)
                )
            )
            await self._rebuild_detail()
        elif wid.startswith("p-del-") and kind == "layer":
            layer = self._layer(key)
            if layer is None:
                return
            idx = int(wid.rsplit("-", 1)[1])
            if idx < len(layer.participants):
                self._mutate_and_refresh(lambda p: layer.participants.pop(idx))
                await self._rebuild_detail()

    # -- structural actions ----------------------------------------------------

    async def action_add_node(self) -> None:
        kind, key = self.selected
        program = self.session.program
        if kind in ("lines-group", "line"):
            new_id = self.session.unique_id("line")
            self._mutate_and_refresh(
                lambda p: p.lines.append(Line(id=new_id, name="New Line")),
            )
            self.selected = ("line", new_id)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
        elif kind in ("layers-group", "layer"):
            base_lines = None
            if kind == "layer":
                current = self._layer(key)
                base_lines = list(current.applies_to) if current else None
            layer = self.session.add_layer(base_lines)
            self.refresh_all()
            self.selected = ("layer", layer.id)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
            self.notify(
                f"attach suggested at {format_money(layer.attach)} "
                f"(top of stack for {'/'.join(layer.applies_to)})"
            )
        elif kind in ("retentions-group", "retention"):
            covered = {lid for r in program.retentions for lid in r.applies_to}
            uncovered = [ln.id for ln in program.lines if ln.id not in covered]
            target = uncovered[:1] or [program.lines[0].id] if program.lines else ["gl"]
            self._mutate_and_refresh(
                lambda p: p.retentions.append(
                    Retention(
                        applies_to=target,
                        type=RetentionType.DEDUCTIBLE,
                        amount=250_000,
                    )
                ),
            )
            self.selected = ("retention", len(program.retentions) - 1)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
        elif kind in ("sublimits-group", "sublimit"):
            first = [program.lines[0].id] if program.lines else ["gl"]
            self._mutate_and_refresh(
                lambda p: p.sublimits.append(
                    Sublimit(name="New Sublimit", amount=1_000_000, applies_to=first)
                ),
            )
            self.selected = ("sublimit", len(program.sublimits) - 1)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()

    async def action_remove_node(self) -> None:
        kind, key = self.selected
        program = self.session.program

        async def after() -> None:
            self.selected = ("program", None)
            self.refresh_all()
            await self._rebuild_detail()

        if kind == "line":
            line = self._line(key)
            if line is None:
                return

            def drop_line(p: Program) -> None:
                p.lines = [ln for ln in p.lines if ln.id != key]
                for layer in p.layers:
                    layer.applies_to = [lid for lid in layer.applies_to if lid != key] or (
                        layer.applies_to
                    )
                p.retentions = [
                    r for r in p.retentions
                    if [lid for lid in r.applies_to if lid != key]
                ]
                for r in p.retentions:
                    r.applies_to = [lid for lid in r.applies_to if lid != key]

            self.session.mutate(drop_line)
            await after()
        elif kind == "layer":
            self.session.mutate(
                lambda p: setattr(p, "layers", [ly for ly in p.layers if ly.id != key])
            )
            await after()
        elif kind == "retention" and key < len(program.retentions):
            self.session.mutate(lambda p: p.retentions.pop(key))
            await after()
        elif kind == "sublimit" and key < len(program.sublimits):
            self.session.mutate(lambda p: p.sublimits.pop(key))
            await after()

    # -- top-level actions -----------------------------------------------------

    async def action_undo(self) -> None:
        if self.session.undo():
            self.refresh_all()
            await self._rebuild_detail()
        else:
            self.notify("nothing to undo")

    async def action_redo(self) -> None:
        if self.session.redo():
            self.refresh_all()
            await self._rebuild_detail()
        else:
            self.notify("nothing to redo")

    def action_save(self) -> None:
        self._drain_focused_input()
        diags = self.session.diagnostics()
        if diags.errors:
            n = len(diags.errors)

            def on_confirm(save_anyway: bool | None) -> None:
                if save_anyway:
                    self._do_save()

            self.app.push_screen(
                ConfirmModal(
                    f"{n} validation error{'s' if n > 1 else ''} present. "
                    "Save the draft anyway?",
                    yes_label="Save anyway",
                ),
                on_confirm,
            )
        else:
            self._do_save()

    def _drain_focused_input(self) -> None:
        # drain-on-save: text sitting in a focused Input must not be lost
        focused = self.focused
        if isinstance(focused, Input):
            self._commit_input(focused)

    def _do_save(self) -> None:
        if self.session.path is None:
            def on_name(name: str | None) -> None:
                if not name:
                    return
                target = Path("programs") / (
                    name if name.endswith(".json") else f"{name}.json"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                self.session.save(target)
                self.notify(f"saved {target}")
                self._refresh_title()

            self.app.push_screen(PromptModal("File name (in programs/):"), on_name)
            return
        self.session.save()
        self.notify(f"saved {self.session.path}")
        self._refresh_title()

    def action_render(self) -> None:
        self._drain_focused_input()
        diags = self.session.diagnostics()
        if diags.errors:
            self.notify(
                f"{len(diags.errors)} validation errors — fix before rendering",
                severity="error",
            )
            return
        from ...render.mpl_program import render_program

        stem = self.session.path.stem if self.session.path else "untitled"
        written = render_program(
            self.session.program, self.tower_theme, Path("dist"), stem, ["svg", "png"]
        )
        self.notify("rendered: " + ", ".join(str(p) for p in written))
        open_cmd = os.environ.get("OPEN_CMD")
        if open_cmd and written:
            subprocess.run(
                [*shlex.split(open_cmd), str(written[0])], check=False
            )

    def action_back(self) -> None:
        if self.session.dirty:
            def on_confirm(leave: bool | None) -> None:
                if leave:
                    self.dismiss_editor()

            self.app.push_screen(
                ConfirmModal("Unsaved changes. Leave without saving?", yes_label="Leave"),
                on_confirm,
            )
        else:
            self.dismiss_editor()

    def dismiss_editor(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()
        else:
            self.app.exit()


_FIELD_HANDLERS = {
    "f-insured": EditorScreen._commit_program_field,
    "f-program": EditorScreen._commit_program_field,
    "f-period-start": EditorScreen._commit_program_field,
    "f-period-end": EditorScreen._commit_program_field,
    "f-line-id": EditorScreen._commit_line_field,
    "f-line-name": EditorScreen._commit_line_field,
    "f-line-abbr": EditorScreen._commit_line_field,
    "f-layer-name": EditorScreen._commit_layer_field,
    "f-layer-attach": EditorScreen._commit_layer_field,
    "f-layer-limit": EditorScreen._commit_layer_field,
    "f-layer-premium": EditorScreen._commit_layer_field,
    "f-ret-amount": EditorScreen._commit_retention_field,
    "f-ret-aggregate": EditorScreen._commit_retention_field,
    "f-ret-vehicle": EditorScreen._commit_retention_field,
    "f-sub-name": EditorScreen._commit_sublimit_field,
    "f-sub-amount": EditorScreen._commit_sublimit_field,
}
