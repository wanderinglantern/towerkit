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

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import (
    Horizontal,
    HorizontalGroup,
    Vertical,
    VerticalGroup,
    VerticalScroll,
)
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    Tree,
)

from ... import edit
from ...model import (
    Line,
    Participant,
    Placement,
    Program,
    RetentionType,
)
from ...money import (
    BPS_SCALE,
    format_money,
    format_money_compact,
    format_share,
    premium_share,
)
from ...theme import load_theme
from ...validate import Diagnostic
from ..session import EditSession, StaleFileError, slugify
from ..theme import (
    AMBER,
    DIM,
    GOLD,
    GREEN,
    RED,
    RULE,
    money_text,
    right,
    severity_text,
    status_text,
)
from ..widgets.inputs import (
    CarrierSuggester,
    MoneyInput,
    ShareValidator,
    known_carriers,
    known_insureds,
    known_layer_names,
    known_line_names,
    parse_share_pct,
)
from ..widgets.modals import (
    ConfirmModal,
    ExitChoiceModal,
    HelpModal,
    PromptModal,
    RenderOptions,
    RenderOptionsModal,
    SendLineModal,
    StaleFileModal,
)
from ..widgets.preview import TowerPreview
from ..widgets.sheet import SheetField, SheetTable

EDITOR_HELP = """towerkit editor — keys

Structure
  a          add an item to the selected group
  delete     remove the selected item
  [ / ]      move a selected LINE left/right (column order)
             (shift+↑/↓ also works if your terminal sends it)
  =          restack: recalculate every attachment so layers sit
             flush on the stack beneath them (heals gaps/overlaps)
  >          send the selected line (and its exclusive
             layers/retentions/sublimits) to another program

Editing
  enter      commit the field you are typing in
  u          undo        ctrl+r  redo

Data sheets
  v          layers sheet: every layer in one grid — name, attach,
             limit and premium edit in place; enter opens the
             selected layer's form; v or esc returns to the form
  i          edit the cell under the cursor, spreadsheet style
             (layers sheet, and the participants grid in a
             layer form)
  tab        commit + hop to the next editable cell
             (shift+tab hops back)
  enter      commit and close the cell editor · esc cancels
  a / del    add / remove the selected row — a participant in
             the participants grid (share defaults to the open
             remainder), a layer in the layers sheet

Output
  r          render (SVG+PNG to dist/)
  t          render options: theme, totals, premiums, per-cell
             premium and policy term, SOI schematic sheet
             (saved with the file)
  x          export SOI workbook (.xlsx to dist/; adds the
             schematic worksheet when enabled under t)

Files
  ctrl+s     save (canonical JSON; prompts if errors exist)
  escape     back / close

?            this help

The dim line above the footer always shows the keys that
apply to the node selected in the structure tree.
"""

NodeRef = tuple[str, Any]

# the visible home for the demoted footer keys: one dim line above the
# footer naming exactly the keys valid for the SELECTED node kind (help
# lists them all — every hidden binding keeps two homes)
NODE_HINTS: dict[str, str] = {
    "program": (
        "[b]t[/b] render options · [b]x[/b] SOI export · [b]=[/b] restack · "
        "[b]v[/b] layers sheet"
    ),
    "lines-group": "[b]a[/b] add line",
    "line": (
        "[b]a[/b] add line · [b]del[/b] remove · [b]\\[ ][/b] move column · "
        "[b]>[/b] send to program"
    ),
    "layers-group": "[b]a[/b] add layer · [b]=[/b] restack · [b]v[/b] layers sheet",
    "layer": (
        "[b]a[/b] add layer · [b]del[/b] remove · [b]=[/b] restack · "
        "[b]v[/b] layers sheet · participants: [b]i[/b] edit cell · "
        "[b]a[/b]/[b]del[/b] row"
    ),
    "retentions-group": "[b]a[/b] add retention",
    "retention": "[b]a[/b] add retention · [b]del[/b] remove",
    "sublimits-group": "[b]a[/b] add sublimit",
    "sublimit": "[b]a[/b] add sublimit · [b]del[/b] remove",
}


def _opts(screen: Screen) -> Any:
    """The running TowerkitApp, which carries the shared render options."""
    return screen.app


class DiagItem(ListItem):
    def __init__(self, label: Label, diag_ref: NodeRef) -> None:
        super().__init__(label)
        self.diag_ref: NodeRef = diag_ref


class EditorScreen(Screen):
    BINDINGS = [
        # visible: the everyday keys. Everything demoted below keeps two
        # homes — the per-node hint line above the footer, and ? help.
        ("ctrl+s", "save", "Save"),
        ("u", "undo", "Undo"),
        ("r", "render", "Render"),
        ("escape", "back", "Back"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("ctrl+r", "redo", "Redo", show=False),
        Binding("t", "render_options", "Options", show=False),
        Binding("x", "export_soi", "SOI", show=False),
        Binding("a", "add_node", "Add", show=False),
        Binding("delete", "remove_node", "Remove", show=False),
        Binding("left_square_bracket", "move_line(-1)", "line left", show=False),
        Binding("right_square_bracket", "move_line(1)", "line right", show=False),
        # kept for terminals that actually transmit shift+arrows; many do not
        Binding("shift+up", "move_line(-1)", show=False),
        Binding("shift+down", "move_line(1)", show=False),
        Binding("equals_sign", "restack", "restack", show=False),
        Binding("greater_than_sign", "send_line", "send", show=False),
        Binding("v", "layers_sheet", "Layers sheet", show=False),
    ]

    DEFAULT_CSS = """
    #context-bar { height: 1; padding: 0 1; background: $panel; color: $text-muted; }
    #panes { height: 1fr; }
    #structure { width: 30; border-right: solid $panel; }
    #detail { width: 46; border-right: solid $panel; padding: 0 1; }
    #preview-pane { width: 1fr; }
    #diagnostics { height: auto; max-height: 6; border-top: solid $panel; }
    #key-hint { height: 1; padding: 0 1; color: $text-muted; }
    .field-label { color: $text-muted; margin-top: 1; }
    .node-diag { margin-top: 1; }
    .row-total { color: $text-muted; }
    Input.-invalid { border: tall $error; }
    #detail Input { width: 1fr; }
    #detail HorizontalGroup > Input { width: 1fr; }
    #detail Button { margin-top: 1; }
    #applies-row { height: auto; }
    .applies-line { height: auto; }
    .applies-line Checkbox { width: 1fr; }
    /* legacy participant inputs: kept mounted (hidden) so the historical
       commit path and its ids (#p-carrier-N, #p-share-N) stay reachable —
       the visible UI is the participants SheetTable */
    .compat-row { display: none; }
    #participants-sheet { height: auto; max-height: 14; }
    #layers-sheet { height: 1fr; }
    .sheet-hint { height: auto; color: $text-muted; margin-top: 1; }
    """

    def __init__(self, session: EditSession, theme_path: Path | None = None) -> None:
        super().__init__()
        self.session = session
        self.selected: NodeRef = ("program", None)
        # the node the CURRENT detail form was built for: field commits use
        # this, never self.selected — a blur-commit racing a tree selection
        # must not resolve against the newly selected node and drop the edit
        self._commit_ref: NodeRef = ("program", None)
        self._detail_lock = asyncio.Lock()
        # the layers data sheet replaces the detail form while open (v toggles)
        self._layers_sheet_open = False
        # a sheet cell commit while its editor is still up (tab-hopping)
        # defers the detail rebuild until the editor closes
        self._sheet_refresh_pending = False
        stored = session.program.render
        if theme_path is None and stored and stored.theme and Path(stored.theme).is_file():
            theme_path = Path(stored.theme)
        self.theme_path = theme_path
        self.tower_theme = load_theme(theme_path)
        self._carriers = known_carriers(
            Path("programs"), Path("themes"), session.program.carriers()
        )
        self._insureds = known_insureds(Path("programs"), [session.program.insured])

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="context-bar")
        with Horizontal(id="panes"):
            yield Tree("Program", id="structure")
            yield VerticalScroll(id="detail")
            with Vertical(id="preview-pane"):
                yield TowerPreview(self.tower_theme, id="preview")
        yield ListView(id="diagnostics")
        yield Static(id="key-hint")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#structure", Tree).tooltip = (
            "Select a line, then [ / ] moves its column left or right. ? for all keys."
        )
        stored = self.session.program.render
        if stored is not None:
            _opts(self).show_totals = stored.show_totals
            _opts(self).show_premiums = stored.show_premiums
            _opts(self).cell_premiums = stored.cell_premiums
            _opts(self).cell_dates = stored.cell_dates
            _opts(self).soi_schematic = stored.soi_schematic
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
        """The 1-line context bar that replaced the stock Header: insured,
        file, placement status, totals, and the unsaved indicator."""
        program = self.session.program
        name = self.session.path.name if self.session.path else "unsaved file"
        sep = Text("  ·  ", style=RULE)
        bar = Text()
        bar.append(program.insured, style=f"bold {GOLD}")
        bar.append("  ")
        bar.append(name, style=DIM)
        bar.append_text(sep)
        bar.append_text(status_text(program.placement.value))
        bar.append_text(sep)
        bar.append(f"limit {format_money_compact(program.total_limit())}")
        bar.append(" · ", style=RULE)
        bar.append(f"premium {format_money_compact(program.total_premium())}")
        bar.append_text(sep)
        if self.session.dirty:
            bar.append("● unsaved", style=f"bold {AMBER}")
        else:
            bar.append("saved", style=DIM)
        self.query_one("#context-bar", Static).update(bar)

    def _refresh_preview(self) -> None:
        self.query_one("#preview", TowerPreview).show_program(
            self.session.program, self.session.diagnostics()
        )

    def _tree_label(self, text: str, diags: list[Diagnostic]) -> Text:
        """Nodes needing review are highlighted, not just marked: ◆ red for
        errors (over-signed, gaps), △ for warnings (under-placed). The
        warning hex is pinned by test_editor_shows_structure_and_diagnostics;
        it sits close enough to the theme AMBER to read as one family."""
        if any(d.severity == "error" for d in diags):
            return Text(f"◆ {text}", style=f"bold {RED}")
        if diags:
            return Text(f"△ {text}", style="#CB7E03")
        return Text(text)

    def _group_label(self, label: str, count: int) -> Text:
        """A tree section header: quiet dim bold, count in dim."""
        return Text.assemble((label, f"bold {DIM}"), (f" ({count})", DIM))

    def _refresh_tree(self) -> None:
        tree = self.query_one("#structure", Tree)
        program = self.session.program
        diags = self.session.diagnostics()
        tree.clear()
        tree.root.data = ("program", None)
        tree.root.label = self._tree_label(program.insured, diags.for_ref(("program", None)))

        lines = tree.root.add(
            self._group_label("Lines", len(program.lines)), data=("lines-group", None)
        )
        for line in program.lines:
            text = line.name if not line.group else f"{line.name} · {line.group}"
            lines.add_leaf(
                self._tree_label(text, diags.for_ref(("line", line.id))),
                data=("line", line.id),
            )

        layers = tree.root.add(
            self._group_label("Layers", len(program.layers)), data=("layers-group", None)
        )
        for layer in sorted(program.layers, key=lambda ly: (ly.attach, ly.id)):
            spans = "/".join(layer.applies_to)
            layers.add_leaf(
                self._tree_label(
                    f"{layer.name} [{spans}]", diags.for_ref(("layer", layer.id))
                ),
                data=("layer", layer.id),
            )

        rets = tree.root.add(
            self._group_label("Retentions", len(program.retentions)),
            data=("retentions-group", None),
        )
        for idx, retention in enumerate(program.retentions):
            rets.add_leaf(
                self._tree_label(
                    f"{'/'.join(retention.applies_to)} {retention.type.value}",
                    diags.for_ref(("retention", idx)),
                ),
                data=("retention", idx),
            )

        subs = tree.root.add(
            self._group_label("Sublimits", len(program.sublimits)),
            data=("sublimits-group", None),
        )
        for idx, sublimit in enumerate(program.sublimits):
            subs.add_leaf(
                self._tree_label(sublimit.name, diags.for_ref(("sublimit", idx))),
                data=("sublimit", idx),
            )

        tree.root.expand_all()

    def _refresh_diagnostics(self) -> None:
        panel = self.query_one("#diagnostics", ListView)
        panel.clear()
        for diag in self.session.diagnostics().items:
            panel.append(
                DiagItem(Label(severity_text(diag.message, diag.severity)), diag.ref)
            )

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
        # a floating cell editor is mounted on the SCREEN — removing its
        # sheet must not orphan it (close = cancel, never a surprise write)
        for sheet in detail.query(SheetTable):
            sheet._close_editor(notify_screen=False)
        self._sheet_refresh_pending = False
        await detail.remove_children()
        if self._layers_sheet_open:
            # the whole-program layers sheet takes over the form pane,
            # widened so seven columns don't clip in the 46-cell form width
            # (2fr against the preview's 1fr keeps `signed` on screen)
            detail.styles.width = "2fr"
            self.query_one("#key-hint", Static).update(
                f"[{DIM}][b]i[/b] edit cell · [b]tab[/b] next cell · "
                "[b]enter[/b] open layer form · [b]a[/b] add layer · "
                "[b]del[/b] remove · [b]v[/b]/[b]esc[/b] back to form · "
                "[b]?[/b] all keys[/]"
            )
            await detail.mount_all(self._layers_sheet_widgets())
            return
        detail.styles.width = 46
        self._commit_ref = self.selected
        # drill-through: the selected node's own issues sit at the top of
        # its form, so a flagged line explains itself when you open it
        node_diags = self.session.diagnostics().for_ref(self.selected)
        for diag in node_diags:
            await detail.mount(
                Label(severity_text(diag.message, diag.severity), classes="node-diag")
            )
        kind, key = self.selected
        self._refresh_hint(kind)
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
        await self._mount_autocompletes(detail, kind)

    def _refresh_hint(self, kind: str) -> None:
        """The hint line above the footer — the visible home for the hidden
        keys that act on the SELECTED node kind."""
        actions = NODE_HINTS.get(kind, NODE_HINTS["program"])
        self.query_one("#key-hint", Static).update(
            f"[{DIM}]{actions} · [b]?[/b] all keys[/]"
        )

    async def _mount_autocompletes(self, detail: VerticalScroll, kind: str) -> None:
        """Dropdown completion from existing records (data consistency):
        insureds, line names, and carriers complete to known spellings.
        Mounted inside #detail, so the next rebuild sweeps them away."""
        from textual_autocomplete import AutoComplete

        mounts: list[tuple[str, list[str]]] = []
        if kind == "program":
            mounts.append(("f-insured", self._insureds))
        elif kind == "line":
            names = known_line_names(
                Path("programs"), [ln.name for ln in self.session.program.lines]
            )
            mounts.append(("f-line-name", names))
        elif kind == "layer":
            layer_names = known_layer_names(
                Path("programs"), [ly.name for ly in self.session.program.layers]
            )
            mounts.append(("f-layer-name", layer_names))
            for widget in detail.query(Input):
                if widget.id and widget.id.startswith("p-carrier-"):
                    mounts.append((widget.id, self._carriers))
        for input_id, candidates in mounts:
            if candidates:
                await detail.mount(
                    AutoComplete(f"#{input_id}", candidates=list(candidates))
                )

    def _form_hint(self, _key: Any) -> list:
        return [
            Static(
                "Select a node to edit it.\n\n"
                f"[{DIM}][b]a[/b] add an item to the selected group\n"
                "[b]del[/b] remove the selected item\n"
                "[b]\\[ ][/b] move a selected line left/right (column order)\n"
                "[b]r[/b] render · [b]ctrl+s[/b] save · "
                "[b]u[/b]/[b]ctrl+r[/b] undo/redo[/]"
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
            Label("Period start / end", classes="field-label"),
            HorizontalGroup(
                Input(
                    value=program.period.start.isoformat(),
                    placeholder="1/1/2026 · Jan 1 2026",
                    id="f-period-start",
                ),
                Input(
                    value=program.period.end.isoformat(),
                    placeholder="1/1/2027 · Jan 1 2027",
                    id="f-period-end",
                ),
            ),
            Label(
                # compact so the line survives the 46-cell pane untruncated
                f"Total limit {format_money_compact(program.total_limit())} · "
                f"premium {format_money_compact(program.total_premium())}",
                classes="row-total",
            ),
        ]

    def _form_line(self, line_id: str) -> list:
        line = self._line(line_id)
        if line is None:
            return self._form_hint(None)
        return [
            Label("Id (auto-generated)", classes="field-label"),
            Input(value=line.id, id="f-line-id", disabled=True),
            Label("Name", classes="field-label"),
            Input(value=line.name, id="f-line-name"),
            Label("Column label", classes="field-label"),
            # pre-populated with the name to speed entry; trim or replace it
            Input(value=line.abbr or line.name, id="f-line-abbr"),
            Label("Group / bucket (project, location…)", classes="field-label"),
            Input(value=line.group or "", id="f-line-group"),
            Label(
                "Reorder: [ moves this column left · ] moves it right",
                classes="row-total",
            ),
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
        layer_names = known_layer_names(
            Path("programs"), [ly.name for ly in self.session.program.layers]
        )
        widgets: list = [
            Label(f"Layer: {layer.name}", classes="field-label"),
            Label("Name", classes="field-label"),
            Input(
                value=layer.name,
                suggester=CarrierSuggester(layer_names) if layer_names else None,
                id="f-layer-name",
            ),
            Label("Policy number", classes="field-label"),
            Input(value=layer.policy_number or "", id="f-layer-policy"),
            Label("Notes (renders as a chart footnote)", classes="field-label"),
            Input(
                value=layer.notes or "",
                placeholder="e.g. 20% open at renewal",
                id="f-layer-notes",
            ),
            Label("SOI limits detail (verbatim in the schedule)", classes="field-label"),
            Input(
                value=layer.limits_detail or "",
                placeholder="e.g. Each Occurrence $1,000,000; Aggregate $2,000,000",
                id="f-layer-limits-detail",
            ),
            Label("SOI deductible / SIR / retention detail", classes="field-label"),
            Input(
                value=layer.retention_detail or "",
                placeholder="e.g. SIR $250,000 each occurrence",
                id="f-layer-retention-detail",
            ),
            Label("Policy period (blank = program period)", classes="field-label"),
            HorizontalGroup(
                Input(
                    value=layer.period.start.isoformat() if layer.period else "",
                    placeholder="start · 1/1/2026",
                    id="f-layer-period-start",
                ),
                Input(
                    value=layer.period.end.isoformat() if layer.period else "",
                    placeholder="end · Jan 1 2027",
                    id="f-layer-period-end",
                ),
            ),
            Label("Applies to", classes="field-label"),
        ]
        widgets.append(self._applies_selector(layer.applies_to))
        widgets += [
            Label("Attach", classes="field-label"),
            Checkbox(
                "Primary — attaches at $0",
                value=layer.attach == 0,
                id="f-layer-primary",
            ),
            MoneyInput(layer.attach, placeholder="2m · 250k · 2,000,000", id="f-layer-attach"),
            Checkbox(
                "Follows underlying (stepped bottom on each line's stack)",
                value=layer.follows_underlying,
                id="f-layer-follows",
            ),
            Label("Limit", classes="field-label"),
            MoneyInput(
                layer.limit if layer.limit > 0 else None,
                placeholder="5m · 25m · 1.5bn",
                id="f-layer-limit",
            ),
            Label("Premium", classes="field-label"),
            MoneyInput(layer.premium, placeholder="125k · $1.2m", id="f-layer-premium"),
            Label("— Participants —", classes="field-label"),
            self._participants_sheet(layer),
            Static(
                f"[{DIM}][b]i[/b] edit cell · [b]a[/b] add · [b]del[/b] remove · "
                "[b]tab[/b] next cell[/]",
                classes="sheet-hint",
            ),
            Static(self._signed_summary(layer), id="signed-summary", classes="row-total"),
        ]
        widgets += self._participant_rows(layer)
        widgets.append(Button("Add participant", id="add-participant"))
        return widgets

    # -- participants sheet ----------------------------------------------------

    def _participants_sheet(self, layer) -> SheetTable:
        """The editable participants grid: carrier · share % · premium share."""
        sheet = SheetTable(id="participants-sheet")
        sheet.cursor_type = "cell"
        sheet.can_insert = True
        sheet.can_delete = True
        sheet.add_column("carrier", key="carrier")
        sheet.add_column(right("share"), key="share")
        sheet.add_column(right("premium"), key="premium")

        def check_share(row_key: str, value) -> str | None:
            # the existing over-sign guard, verbatim semantics: block at the
            # input (editor stays open), never flag after the fact
            if value is None:
                return None  # required-ness already rejected empties
            idx = int(row_key)
            others = sum(
                p.share_bps for i, p in enumerate(layer.participants) if i != idx
            )
            if others + int(value) > BPS_SCALE:
                return f"over-signed: {format_share(others + int(value))} > 100%"
            return None

        sheet.fields = {
            0: SheetField(
                "carrier",
                "carrier",
                required=True,
                suggester=CarrierSuggester(self._carriers),
                dropdown=list(self._carriers) or None,
            ),
            1: SheetField("share", "share", "share", required=True, validate=check_share),
        }
        sheet.initial_text = self._participant_cell_text
        for idx, participant in enumerate(layer.participants):
            sheet.add_row(*self._participant_cells(layer, participant), key=str(idx))
        return sheet

    def _participant_cells(self, layer, participant) -> tuple:
        prem = (
            money_text(premium_share(layer.premium, participant.share_bps))
            if layer.premium is not None
            else Text("—", style=DIM, justify="right")
        )
        return (
            participant.carrier,
            Text(format_share(participant.share_bps), justify="right"),
            prem,
        )

    def _participant_cell_text(self, row_key: str, field_key: str) -> str:
        """Editor prefill for a participants-sheet cell."""
        kind, key = self._commit_ref
        layer = self._layer(key) if kind == "layer" else None
        if layer is None:
            return ""
        idx = int(row_key)
        if idx >= len(layer.participants):
            return ""
        participant = layer.participants[idx]
        if field_key == "carrier":
            return str(participant.carrier)
        return f"{participant.share_bps / 100:g}"

    def _participant_rows(self, layer) -> list:
        """Hidden legacy inputs (#p-carrier-N / #p-share-N / #p-prem-N): the
        visible UI is the SheetTable, but the historical commit path stays
        mounted and functional — it is pinned by existing tests and keeps the
        blur-commit machinery reachable for tooling."""
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
                        placeholder="carrier",
                        id=f"p-carrier-{idx}",
                    ),
                    Input(
                        value=f"{participant.share_bps / 100:g}",
                        validators=[ShareValidator()],
                        placeholder="% · 35 or 33.33",
                        id=f"p-share-{idx}",
                    ),
                    Static(prem, id=f"p-prem-{idx}"),
                    classes="compat-row",
                )
            )
        return rows

    def _signed_summary(self, layer) -> Text:
        """The signed-total line: green when fully signed, amber with the open
        remainder, red when over-signed — glyph+word so it survives
        monochrome terminals."""
        signed = layer.signed_bps
        if signed == BPS_SCALE:
            return Text(f"signed {format_share(signed)}", style=GREEN)
        if signed > BPS_SCALE:
            return Text(f"◆ over-signed {format_share(signed)}", style=f"bold {RED}")
        open_bps = BPS_SCALE - signed
        return Text(
            f"signed {format_share(signed)} — {format_share(open_bps)} open",
            style=AMBER,
        )

    # -- layers sheet ----------------------------------------------------------

    def _layers_sheet_widgets(self) -> list:
        """The whole-program grid: one row per layer, in model order."""
        program = self.session.program
        sheet = SheetTable(id="layers-sheet")
        sheet.cursor_type = "row"
        # a adds a layer, del removes the cursor row — session-backed, u undoes;
        # consuming the keys here keeps them from acting on the tree selection
        sheet.can_insert = True
        sheet.can_delete = True
        sheet.add_column("layer", key="name")
        sheet.add_column("lines", key="lines")
        sheet.add_column(right("attach"), key="attach")
        sheet.add_column(right("limit"), key="limit")
        sheet.add_column(right("top"), key="top")
        sheet.add_column(right("premium"), key="premium")
        sheet.add_column(right("signed"), key="signed")
        layer_names = known_layer_names(
            Path("programs"), [ly.name for ly in program.layers]
        )
        name_suggester = CarrierSuggester(layer_names) if layer_names else None
        sheet.fields = {
            0: SheetField("name", "name", required=True, suggester=name_suggester),
            2: SheetField("attach", "attach", "money", required=True),
            3: SheetField("limit", "limit", "money", required=True),
            5: SheetField("premium", "premium", "money"),
        }
        sheet.initial_text = self._layer_cell_text
        for layer in program.layers:
            sheet.add_row(*self._layer_cells(layer), key=layer.id)
        return [
            sheet,
            Static(
                f"[{DIM}]lines · top · signed are derived — edit them through "
                "the layer form and participants[/]",
                classes="sheet-hint",
            ),
        ]

    def _layer_cells(self, layer) -> tuple:
        return (
            layer.name,
            Text("/".join(layer.applies_to), style=DIM),
            # attach prints even at $0 — a primary's attachment is data, not absence
            Text(format_money_compact(layer.attach), justify="right"),
            money_text(layer.limit),
            money_text(layer.top),
            money_text(layer.premium),
            self._signed_cell(layer),
        )

    def _signed_cell(self, layer) -> Text:
        signed = layer.signed_bps
        if signed == BPS_SCALE:
            return Text(format_share(signed), style=GREEN, justify="right")
        if signed > BPS_SCALE:
            return Text(f"◆ {format_share(signed)}", style=f"bold {RED}", justify="right")
        return Text(f"△ {format_share(signed)}", style=AMBER, justify="right")

    def _layer_cell_text(self, row_key: str, field_key: str) -> str:
        """Editor prefill for a layers-sheet cell."""
        layer = self._layer(row_key)
        if layer is None:
            return ""
        if field_key == "name":
            return str(layer.name)
        if field_key == "attach":
            return format_money(layer.attach)
        if field_key == "limit":
            return format_money(layer.limit) if layer.limit else ""
        if field_key == "premium":
            return format_money(layer.premium) if layer.premium is not None else ""
        return ""

    async def action_layers_sheet(self) -> None:
        self._drain_focused_input()
        self._layers_sheet_open = not self._layers_sheet_open
        await self._rebuild_detail()
        if self._layers_sheet_open:
            try:
                self.query_one("#layers-sheet", SheetTable).focus()
            except Exception:
                pass
        else:
            self.query_one("#structure", Tree).focus()

    # -- sheet message plumbing ------------------------------------------------

    async def _flush_sheet_rebuild(self, table_id: str, cursor=None) -> None:
        """Rebuild the detail pane and put the cursor (and focus) back on the
        sheet the user was working in."""
        if cursor is None:
            try:
                cursor = self.query_one(f"#{table_id}", SheetTable).cursor_coordinate
            except Exception:
                cursor = None
        await self._rebuild_detail()
        try:
            sheet = self.query_one(f"#{table_id}", SheetTable)
        except Exception:
            return
        if cursor is not None and sheet.row_count:
            sheet.move_cursor(
                row=min(cursor.row, sheet.row_count - 1), column=cursor.column
            )
        sheet.focus()

    @on(SheetTable.EditorClosed)
    async def _sheet_editor_closed(self, event: SheetTable.EditorClosed) -> None:
        if self._sheet_refresh_pending:
            self._sheet_refresh_pending = False
            await self._flush_sheet_rebuild(event.table.id or "")

    def _sync_participants_sheet(self, layer) -> None:
        """Refresh the sheet's cells in place (no rebuild — an open editor
        must keep its anchor row)."""
        try:
            sheet = self.query_one("#participants-sheet", SheetTable)
        except Exception:
            return
        if sheet.row_count != len(layer.participants):
            return  # structure changed: a rebuild is coming, don't guess
        for idx, participant in enumerate(layer.participants):
            carrier, share, prem = self._participant_cells(layer, participant)
            try:
                sheet.update_cell(str(idx), "carrier", carrier)
                sheet.update_cell(str(idx), "share", share)
                sheet.update_cell(str(idx), "premium", prem)
            except Exception:
                return
        try:
            self.query_one("#signed-summary", Static).update(self._signed_summary(layer))
        except Exception:
            pass

    @on(SheetTable.CellEdited, "#participants-sheet")
    async def _participant_cell_edited(self, event: SheetTable.CellEdited) -> None:
        kind, key = self._commit_ref
        layer = self._layer(key) if kind == "layer" else None
        if layer is None:
            return
        idx = int(event.row_key)
        if idx >= len(layer.participants):
            return
        participant = layer.participants[idx]
        if event.field.key == "carrier":
            value = str(event.value)
            if value != participant.carrier:
                self._mutate_and_refresh(lambda p: setattr(participant, "carrier", value))
                if value not in self._carriers:
                    self._carriers.append(value)
        elif event.field.key == "share" and isinstance(event.value, int):
            bps = event.value  # over-signing was vetoed before the post
            if bps != participant.share_bps:
                self._mutate_and_refresh(
                    lambda p: setattr(participant, "share_bps", bps)
                )
        if event.table.editing:
            # tab is walking the row — rebuild when the editor closes
            self._sheet_refresh_pending = True
            self._sync_participants_sheet(layer)
        else:
            await self._flush_sheet_rebuild("participants-sheet")

    @on(SheetTable.InsertRequested, "#participants-sheet")
    async def _participant_insert(self, event: SheetTable.InsertRequested) -> None:
        kind, key = self._commit_ref
        layer = self._layer(key) if kind == "layer" else None
        if layer is None:
            return
        open_bps = max(0, BPS_SCALE - layer.signed_bps)
        self._mutate_and_refresh(
            lambda p: layer.participants.append(
                Participant(carrier="New Carrier", share_bps=open_bps)
            )
        )
        await self._flush_sheet_rebuild(
            "participants-sheet",
            cursor=Coordinate(len(layer.participants) - 1, 0),
        )

    @on(SheetTable.DeleteRequested, "#participants-sheet")
    async def _participant_delete(self, event: SheetTable.DeleteRequested) -> None:
        kind, key = self._commit_ref
        layer = self._layer(key) if kind == "layer" else None
        if layer is None:
            return
        idx = int(event.row_key)
        if idx >= len(layer.participants):
            return
        self._mutate_and_refresh(lambda p: layer.participants.pop(idx))
        await self._flush_sheet_rebuild("participants-sheet")

    @on(SheetTable.CellEdited, "#layers-sheet")
    async def _layer_cell_edited(self, event: SheetTable.CellEdited) -> None:
        layer = self._layer(event.row_key)
        if layer is None:
            return
        field_key = event.field.key
        if field_key == "name":
            value = str(event.value)
            if value != layer.name:

                def rename(p: Program) -> None:
                    layer.name = value
                    # Same rule as lines: the id follows the name rather than
                    # freezing after the first commit. Nothing in the file
                    # points at a layer id — appliesTo always holds LINE ids —
                    # so there is nothing to cascade here.
                    layer.id = self.session.unique_id(slugify(value), exclude=layer.id)

                self._mutate_and_refresh(rename)
                if self.selected == ("layer", event.row_key):
                    self.selected = ("layer", layer.id)
        elif field_key == "attach" and event.value is not None:
            amount = int(event.value)
            self._mutate_and_refresh(lambda p: setattr(layer, "attach", amount))
        elif field_key == "limit" and event.value is not None:
            amount = int(event.value)
            self._mutate_and_refresh(lambda p: setattr(layer, "limit", amount))
        elif field_key == "premium":
            premium = int(event.value) if event.value is not None else None
            self._mutate_and_refresh(lambda p: setattr(layer, "premium", premium))
        if event.table.editing:
            self._sheet_refresh_pending = True
            # refresh this row in place; a renamed placeholder id makes the
            # row key stale, in which case the pending rebuild reconciles
            try:
                for column_key, cell in zip(
                    ("name", "lines", "attach", "limit", "top", "premium", "signed"),
                    self._layer_cells(layer),
                    strict=True,
                ):
                    event.table.update_cell(event.row_key, column_key, cell)
            except Exception:
                pass
        else:
            await self._flush_sheet_rebuild("layers-sheet")

    @on(SheetTable.InsertRequested, "#layers-sheet")
    async def _layers_sheet_insert(self, event: SheetTable.InsertRequested) -> None:
        layer = self.session.add_layer(None)
        self.refresh_all()
        await self._flush_sheet_rebuild(
            "layers-sheet",
            cursor=Coordinate(len(self.session.program.layers) - 1, 0),
        )
        self.notify(
            f"attach suggested at {format_money(layer.attach)} "
            f"(top of stack for {'/'.join(layer.applies_to)})"
        )

    @on(SheetTable.DeleteRequested, "#layers-sheet")
    async def _layers_sheet_delete(self, event: SheetTable.DeleteRequested) -> None:
        layer_id = event.row_key
        if self._layer(layer_id) is None:
            return
        if self.selected == ("layer", layer_id):
            self.selected = ("program", None)
        self._mutate_and_refresh(lambda p: edit.remove_layer(p, layer_id))
        await self._flush_sheet_rebuild("layers-sheet")

    @on(SheetTable.RowSelected, "#layers-sheet")
    async def _layers_sheet_row_selected(self, event) -> None:
        """Enter on a layer row: jump the outline there and reopen the form."""
        layer_id = str(event.row_key.value) if event.row_key else ""
        if self._layer(layer_id) is None:
            return
        self._layers_sheet_open = False
        self.selected = ("layer", layer_id)
        await self._rebuild_detail()
        self._select_tree_node(self.selected)

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
            MoneyInput(retention.amount, placeholder="250k · 1m", id="f-ret-amount"),
            Label("Aggregate (optional)", classes="field-label"),
            MoneyInput(
                retention.aggregate, placeholder="blank = none · 2m", id="f-ret-aggregate"
            ),
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
            MoneyInput(sublimit.amount, placeholder="1m · 500k", id="f-sub-amount"),
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
        kind, key = self._commit_ref
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
            from ...dates import parse_flexible_date

            parsed = parse_flexible_date(value)
            if parsed is None:
                self.notify(f"can't read {value!r} as a date", severity="error")
                return
            widget.value = parsed.isoformat()  # echo back the canonical form
            attr = "start" if wid == "f-period-start" else "end"

            def set_period(p: Program) -> None:
                p.period = p.period.model_copy(update={attr: parsed})

            self._mutate_and_refresh(set_period)

    def _commit_line_field(self, widget: Input) -> None:
        kind, key = self._commit_ref
        line = self._line(key)
        if line is None:
            return
        value = widget.value.strip()
        if widget.id == "f-line-name" and value and value != line.name:
            # The id ALWAYS follows the name, cascading every reference. This
            # used to be gated on the id still looking like a placeholder
            # ("line-2"), which meant the first committed name burned the one
            # chance to set it — a typo in that name was then permanent, and
            # the id field is deliberately not editable. edit.rename_line owns
            # the cascade; appliesTo on layers, retentions and sublimits is the
            # complete set of things in the file holding a line id.
            old = line.id
            new_id: list[str] = []

            def do(p: Program) -> None:
                new_id.append(edit.rename_line(p, old, value).id)

            self._mutate_and_refresh(do)
            self.selected = ("line", new_id[0])
            self._select_tree_node(self.selected)
        elif widget.id == "f-line-abbr":
            self._mutate_and_refresh(lambda p: setattr(line, "abbr", value or None))
        elif widget.id == "f-line-group":
            self._mutate_and_refresh(lambda p: setattr(line, "group", value or None))

    def _commit_layer_field(self, widget: Input) -> None:
        kind, key = self._commit_ref
        layer = self._layer(key)
        if layer is None:
            return
        wid = widget.id
        if wid == "f-layer-name":
            value = widget.value.strip()
            if value and value != layer.name:
                def rename(p: Program) -> None:
                    layer.name = value
                    # Same rule as lines: the id follows the name rather than
                    # freezing after the first commit. Nothing in the file
                    # points at a layer id — appliesTo always holds LINE ids —
                    # so there is nothing to cascade here.
                    layer.id = self.session.unique_id(slugify(value), exclude=layer.id)

                self._mutate_and_refresh(rename)
                self.selected = ("layer", layer.id)
                self._select_tree_node(self.selected)
            return
        if wid == "f-layer-policy":
            policy = widget.value.strip() or None
            self._mutate_and_refresh(lambda p: setattr(layer, "policy_number", policy))
            return
        if wid == "f-layer-notes":
            note = widget.value.strip() or None
            self._mutate_and_refresh(lambda p: setattr(layer, "notes", note))
            return
        if wid == "f-layer-limits-detail":
            limits_detail = widget.value.strip() or None
            self._mutate_and_refresh(lambda p: setattr(layer, "limits_detail", limits_detail))
            return
        if wid == "f-layer-retention-detail":
            retention_detail = widget.value.strip() or None
            self._mutate_and_refresh(
                lambda p: setattr(layer, "retention_detail", retention_detail)
            )
            return
        if wid in ("f-layer-period-start", "f-layer-period-end"):
            self._commit_layer_period(layer)
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

    def _commit_layer_period(self, layer) -> None:
        from ...dates import parse_flexible_date

        start_widget = self.query_one("#f-layer-period-start", Input)
        end_widget = self.query_one("#f-layer-period-end", Input)
        start_text, end_text = start_widget.value.strip(), end_widget.value.strip()
        if not start_text and not end_text:
            self._mutate_and_refresh(lambda p: setattr(layer, "period", None))
            return
        if not (start_text and end_text):
            return  # half-entered; wait for the other field
        start, end = parse_flexible_date(start_text), parse_flexible_date(end_text)
        if start is None or end is None:
            bad = start_text if start is None else end_text
            self.notify(f"can't read {bad!r} as a date", severity="error")
            return
        start_widget.value, end_widget.value = start.isoformat(), end.isoformat()
        from ...model import Period

        self._mutate_and_refresh(
            lambda p: setattr(layer, "period", Period(start=start, end=end))
        )

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
        self._sync_participants_sheet(layer)

    def _commit_retention_field(self, widget: Input) -> None:
        kind, index = self._commit_ref
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
        kind, index = self._commit_ref
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
        kind, key = self._commit_ref
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
                self._sync_participants_sheet(layer)
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
        if wid == "f-layer-primary":
            kind, key = self._commit_ref
            layer = self._layer(key) if kind == "layer" else None
            if layer is None:
                return
            target = layer  # mypy: closures don't keep the None-narrowing
            if event.value:
                def make_primary(p: Program) -> None:
                    target.attach = 0
                    target.follows_underlying = False

                self._mutate_and_refresh(make_primary)
            else:
                # back to an excess: suggest the top of the stack beneath it
                others = [
                    ly.top
                    for ly in self.session.program.layers
                    if ly.id != layer.id
                    and ly.limit > 0
                    and any(lid in ly.applies_to for lid in layer.applies_to)
                ]
                suggestion = max(others, default=0)
                self._mutate_and_refresh(lambda p: setattr(layer, "attach", suggestion))
                if suggestion:
                    self.notify(f"attach suggested at {format_money(suggestion)}")
            try:
                self.query_one("#f-layer-attach", MoneyInput).set_amount(layer.attach)
            except Exception:
                pass
            return
        if wid == "f-layer-follows":
            kind, key = self._commit_ref
            layer = self._layer(key) if kind == "layer" else None
            if layer is None:
                return
            flag = bool(event.value)

            def set_follows(p: Program) -> None:
                layer.follows_underlying = flag
                if flag:
                    # snap attach to the highest underlying top so the
                    # validator is satisfied by construction
                    layer.attach = max(
                        p.underlying_tops(layer).values(), default=0
                    )

            self._mutate_and_refresh(set_follows)
            try:
                self.query_one("#f-layer-attach", MoneyInput).set_amount(layer.attach)
            except Exception:
                pass
            return
        if not wid.startswith("applies-"):
            return
        line_id = wid.removeprefix("applies-")
        kind, key = self._commit_ref
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
            kind, index = self._commit_ref
            if kind == "retention" and index < len(self.session.program.retentions):
                retention = self.session.program.retentions[index]
                self._mutate_and_refresh(
                    lambda p: setattr(retention, "type", RetentionType(str(value)))
                )

    # -- buttons ---------------------------------------------------------------

    @on(Button.Pressed)
    async def _button_pressed(self, event: Button.Pressed) -> None:
        wid = event.button.id or ""
        kind, key = self._commit_ref
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

    # -- structural actions ----------------------------------------------------

    async def action_add_node(self) -> None:
        kind, key = self.selected
        program = self.session.program
        if kind in ("lines-group", "line"):
            created: list[str] = []

            def add_line(p: Program) -> None:
                # placeholder id, not slugified from the throwaway display
                # name — the first real name commit (in _commit_line_field)
                # re-slugs it, and every commit after that does too.
                new_line = edit.add_line(p, "New Line")
                new_line.id = edit.unique_id(p, "line", exclude=new_line.id)
                created.append(new_line.id)

            self._mutate_and_refresh(add_line)
            self.selected = ("line", created[0])
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
            target = (uncovered[:1] or [program.lines[0].id]) if program.lines else ["gl"]
            self._mutate_and_refresh(
                lambda p: edit.add_retention(p, target, RetentionType.DEDUCTIBLE, 250_000)
            )
            self.selected = ("retention", len(program.retentions) - 1)
            self._select_tree_node(self.selected)
            await self._rebuild_detail()
        elif kind in ("sublimits-group", "sublimit"):
            first = [program.lines[0].id] if program.lines else ["gl"]
            self._mutate_and_refresh(
                lambda p: edit.add_sublimit(p, "New Sublimit", 1_000_000, first)
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
            if self._line(key) is None:
                return
            self.session.mutate(lambda p: edit.remove_line(p, key))
            await after()
        elif kind == "layer":
            if self._layer(key) is None:
                return
            self.session.mutate(lambda p: edit.remove_layer(p, key))
            await after()
        elif kind == "retention" and key < len(program.retentions):
            self.session.mutate(lambda p: edit.remove_retention(p, key))
            await after()
        elif kind == "sublimit" and key < len(program.sublimits):
            self.session.mutate(lambda p: edit.remove_sublimit(p, key))
            await after()

    # -- top-level actions -----------------------------------------------------

    async def action_move_line(self, delta: int) -> None:
        """Reorder coverage lines — array order is column order in the viz."""
        kind, key = self.selected
        if kind != "line":
            self.notify("select a line to reorder columns")
            return
        lines = self.session.program.lines
        index = next((i for i, ln in enumerate(lines) if ln.id == key), None)
        if index is None:
            return
        target = None

        def do(p: Program) -> None:
            nonlocal target
            target = edit.move_line(p, key, delta)

        self.session.mutate(do)
        if target is None or target == index:
            return
        self.refresh_all()
        self._select_tree_node(("line", key))
        self.notify(f"{lines[target].name} → column {target + 1} of {len(lines)}")

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
                if target.exists():
                    self.notify(
                        f"{target} already exists — pick another name",
                        severity="error",
                    )
                    return
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    self.session.save(target)
                except OSError as exc:
                    self._notify_save_failure(exc)
                    return
                self.notify(f"saved {target}")
                self._refresh_title()

            self.app.push_screen(PromptModal("File name (in programs/):"), on_name)
            return
        self._save_guarded()

    def _notify_save_failure(self, exc: OSError) -> None:
        """A save that cannot reach disk is a message, never a crash — the
        session still holds every edit, and killing the app would throw
        them away on top of the failure."""
        reason = exc.strerror or str(exc)
        self.notify(
            f"could not save {self.session.path}: {reason} — your edits are "
            f"still here; try ctrl+s again, or use a different location",
            severity="error",
            timeout=10,
        )

    def _reload_guarded(self) -> None:
        """Take what is on disk. The file may have been deleted rather than
        merely changed, and a bad or missing file must not cost the
        session that is still holding the user's work."""
        try:
            self.session.reload()
        except (OSError, ValueError) as exc:
            self.notify(
                f"could not reload {self.session.path}: {exc} — your edits "
                f"are still here",
                severity="error",
                timeout=10,
            )
            return
        self.refresh_all()
        self.notify("reloaded from disk — your edits were discarded")

    def _save_guarded(self, then: Callable[[], None] | None = None) -> None:
        """Every save that overwrites the loaded file goes through here.
        StaleFileError is a question for the user, not an error to swallow;
        an OSError is a message, not a crash."""

        def done() -> None:
            self.notify(f"saved {self.session.path}")
            self._refresh_title()
            if then is not None:
                then()

        try:
            self.session.save()
        except StaleFileError:
            if self.session.path is not None and not self.session.path.exists():
                # deleted or renamed underneath us: there is nothing to
                # clobber, so write it back rather than asking a question
                # whose "reload" answer has nothing left to read
                try:
                    self.session.save(force=True)
                except OSError as exc:
                    self._notify_save_failure(exc)
                    return
                self.notify(f"{self.session.path} had been removed — wrote it back")
                self._refresh_title()
                if then is not None:
                    then()
                return

            def on_choice(choice: str | None) -> None:
                if choice == "overwrite":
                    try:
                        self.session.save(force=True)
                    except OSError as exc:
                        self._notify_save_failure(exc)
                        return
                    done()
                elif choice == "reload":
                    self._reload_guarded()
                # anything else: keep editing

            self.app.push_screen(StaleFileModal(), on_choice)
            return
        except OSError as exc:
            self._notify_save_failure(exc)
            return
        done()

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
        try:
            written = render_program(
                self.session.program, self.tower_theme, Path("dist"), stem, ["svg", "png"],
                show_totals=_opts(self).show_totals, show_premiums=_opts(self).show_premiums,
                cell_premiums=getattr(_opts(self), "cell_premiums", False),
                cell_dates=getattr(_opts(self), "cell_dates", False),
            )
        except Exception as exc:
            self.notify(f"render failed: {exc}", severity="error")
            return
        self.notify("rendered: " + ", ".join(str(p) for p in written))
        open_cmd = os.environ.get("OPEN_CMD")
        if open_cmd and written:
            subprocess.run(
                [*shlex.split(open_cmd), str(written[0])], check=False
            )

    def action_render_options(self) -> None:
        def on_choice(options: RenderOptions | None) -> None:
            if options is None:
                return
            _opts(self).show_totals = options.show_totals
            _opts(self).show_premiums = options.show_premiums
            _opts(self).cell_premiums = options.cell_premiums
            _opts(self).cell_dates = options.cell_dates
            _opts(self).soi_schematic = options.soi_schematic
            self.apply_theme(Path(options.theme) if options.theme else None)
            from ...model import RenderSettings

            settings = RenderSettings(
                theme=options.theme or None,
                show_totals=options.show_totals,
                show_premiums=options.show_premiums,
                cell_premiums=options.cell_premiums,
                cell_dates=options.cell_dates,
                soi_schematic=options.soi_schematic,
            )
            # persist with the program so next session opens the same way
            self.session.mutate(lambda p: setattr(p, "render", settings))
            self.refresh_all()

        self.app.push_screen(
            RenderOptionsModal(
                self.theme_path, _opts(self).show_totals, _opts(self).show_premiums,
                _opts(self).cell_premiums, getattr(_opts(self), "cell_dates", False),
                getattr(_opts(self), "soi_schematic", False),
            ),
            on_choice,
        )

    def action_export_soi(self) -> None:
        self._drain_focused_input()
        diags = self.session.diagnostics()
        if diags.errors:
            self.notify(
                f"{len(diags.errors)} validation errors — fix before exporting",
                severity="error",
            )
            return
        from ...render.soi_xlsx import write_soi_workbook
        from ...soi import default_filename

        program = self.session.program
        try:
            written = write_soi_workbook(
                program,
                theme=self.tower_theme,
                out_path=Path("dist") / default_filename(program),
                show_premiums=_opts(self).show_premiums,
                include_schematic=getattr(_opts(self), "soi_schematic", False),
            )
        except Exception as exc:
            self.notify(f"export failed: {exc}", severity="error")
            return
        self.notify(f"exported: {written}")
        open_cmd = os.environ.get("OPEN_CMD")
        if open_cmd:
            subprocess.run([*shlex.split(open_cmd), str(written)], check=False)

    def action_send_line(self) -> None:
        self._drain_focused_input()
        kind, key = self.selected
        if kind != "line":
            self.notify("select a line to send", severity="warning")
            return
        line = self._line(key)
        if line is None:
            return
        targets = sorted(Path("programs").glob("*.json")) + sorted(
            (Path("programs") / "private").glob("*.json")
        )
        if self.session.path is not None:
            targets = [
                p for p in targets if p.resolve() != self.session.path.resolve()
            ]
        if not targets:
            self.notify("no other program files under programs/", severity="warning")
            return

        def on_target(choice: tuple[Path, bool] | None) -> None:
            if choice is None:
                return
            target_path, move = choice
            from ...model import dump_program, load_program
            from ...transfer import transfer_line
            from ...validate import validate_program

            try:
                dst = load_program(target_path)
            except Exception as exc:
                self.notify(f"can't read {target_path}: {exc}", severity="error")
                return
            if validate_program(dst).errors:
                self.notify(
                    f"{target_path.name} has validation errors — fix it first",
                    severity="error",
                )
                return
            try:
                result = transfer_line(self.session.program, dst, line.id, move=move)
            except Exception as exc:
                self.notify(f"transfer failed: {exc}", severity="error")
                return
            lines = [f"{'Move' if move else 'Copy'} to {target_path.name}:"]
            lines += [f"  + {t}" for t in result.summary.travels]
            lines += [f"  stays: {s}" for s in result.summary.stays]
            lines += [
                f"  renamed in target: {old} → {new}"
                for old, new in result.summary.renames
            ]
            target_diags = validate_program(result.dst_after)
            if target_diags.errors:
                lines.append(
                    f"  target will have {len(target_diags.errors)} "
                    "validation error(s):"
                )
                lines += [f"    {d.message}" for d in target_diags.errors]

            async def on_confirm(go: bool | None) -> None:
                if not go:
                    return
                try:
                    dump_program(result.dst_after, target_path)
                except Exception as exc:
                    self.notify(f"write failed: {exc}", severity="error")
                    return
                if move:
                    def apply_src(p: Program) -> None:
                        edit.adopt(p, result.src_after)

                    self.session.mutate(apply_src)
                    # the moved line's node is gone from the tree; fall back
                    # to the program-level selection like action_remove_node
                    # does, and rebuild the detail pane so it doesn't keep
                    # showing a form for a line that no longer exists
                    self.selected = ("program", None)
                    self.refresh_all()
                    await self._rebuild_detail()
                verb = "moved" if move else "copied"
                self.notify(f"{verb} {line.name} to {target_path.name}")

            self.app.push_screen(
                ConfirmModal("\n".join(lines), yes_label="Send"), on_confirm
            )

        self.app.push_screen(SendLineModal(line.name, targets), on_target)

    def apply_theme(self, theme_path: Path | None) -> None:
        self.theme_path = theme_path
        self.tower_theme = load_theme(theme_path)
        preview = self.query_one("#preview", TowerPreview)
        preview.tower_theme = self.tower_theme
        self._refresh_preview()
        parts = [f"theme: {self.tower_theme.name}"]
        if not _opts(self).show_totals:
            parts.append("totals hidden")
        if not _opts(self).show_premiums:
            parts.append("premiums hidden")
        self.notify(" · ".join(parts))

    def action_restack(self) -> None:
        self.session.restack()
        self.refresh_all()
        self.notify("attachments recalculated from the stacking order")

    def action_help(self) -> None:
        self.app.push_screen(HelpModal(EDITOR_HELP))

    async def action_back(self) -> None:
        if self._layers_sheet_open:
            # esc from the layers sheet returns to the form, never exits
            await self.action_layers_sheet()
            return
        # Drain FIRST. Text sitting in a focused Input has not reached the
        # model yet, so `dirty` cannot see it — checking dirty before
        # draining is exactly how typed-but-uncommitted edits used to
        # vanish on esc, with no prompt and no undo.
        self._drain_focused_input()
        if not self.session.dirty:
            self.dismiss_editor()
            return

        def on_choice(choice: str | None) -> None:
            if choice == "discard":
                self.dismiss_editor()
                return
            if choice != "save":
                return  # keep editing
            diags = self.session.diagnostics()
            if diags.errors:
                self.notify(
                    f"{len(diags.errors)} validation error"
                    f"{'s' if len(diags.errors) > 1 else ''} — fix them "
                    "(or choose Discard); nothing was lost",
                    severity="error",
                )
                return  # stay in the editor with the changes intact
            if self.session.path is None:
                self.notify(
                    "no file name yet — ctrl+s saves-as first", severity="warning"
                )
                return
            self._save_guarded(then=self.dismiss_editor)

        self.app.push_screen(ExitChoiceModal(), on_choice)

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
    "f-line-name": EditorScreen._commit_line_field,
    "f-line-abbr": EditorScreen._commit_line_field,
    "f-line-group": EditorScreen._commit_line_field,
    "f-layer-name": EditorScreen._commit_layer_field,
    "f-layer-policy": EditorScreen._commit_layer_field,
    "f-layer-notes": EditorScreen._commit_layer_field,
    "f-layer-limits-detail": EditorScreen._commit_layer_field,
    "f-layer-retention-detail": EditorScreen._commit_layer_field,
    "f-layer-period-start": EditorScreen._commit_layer_field,
    "f-layer-period-end": EditorScreen._commit_layer_field,
    "f-layer-attach": EditorScreen._commit_layer_field,
    "f-layer-limit": EditorScreen._commit_layer_field,
    "f-layer-premium": EditorScreen._commit_layer_field,
    "f-ret-amount": EditorScreen._commit_retention_field,
    "f-ret-aggregate": EditorScreen._commit_retention_field,
    "f-ret-vehicle": EditorScreen._commit_retention_field,
    "f-sub-name": EditorScreen._commit_sublimit_field,
    "f-sub-amount": EditorScreen._commit_sublimit_field,
}
