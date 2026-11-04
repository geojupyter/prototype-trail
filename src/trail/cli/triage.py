from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from functools import cached_property, partial
from pathlib import Path
from types import NotImplementedType

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples, fragment_list_to_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import (
    BufferControl,
    Dimension,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from trail import fmt
from trail.cli.command import Query
from trail.cli.render import Renderer
from trail.cli.theme import INDENT, STYLE, VERB_STYLES
from trail.event import Event
from trail.trail import Trail

ACCEPTED = 'Accepted'
PENDING = 'Pending'
DECLINED = 'Declined'


class Pane(FormattedTextControl):
    def __init__(
        self,
        triage: Triage,
        name: str,
    ) -> None:
        self.triage = triage
        self.name = name
        self.ids: list[str] = []
        self.total = 0
        self.selected: str | None = None
        super().__init__(self.rows, focusable=True, get_cursor_position=self.position)
        self.window = Window(
            self,
            wrap_lines=False,
            always_hide_cursor=True,
            right_margins=[ScrollbarMargin()],
        )
        self.header = Window(
            FormattedTextControl(self.heading),
            height=1,
            style=self.header_style,
        )
        self.container = HSplit([
            self.header,
            self.window,
        ], width=Dimension(preferred=1, weight=1))

    def heading(self) -> str:
        out = f' {self.name} · {len(self.ids)}'
        if self.triage.matches is not None:
            out += f' of {self.total}'
        return out

    def header_style(self) -> str:
        out = f'class:triage.header.{self.name.lower()}'
        if self.triage.focused is self:
            out += '.active'
        return out

    def position(self) -> Point:
        row = 0
        if self.selected in self.ids:
            row = self.ids.index(self.selected)
        out = Point(x=0, y=row)
        return out

    def replace(
        self,
        identifiers: list[str],
        follow: bool = False,
    ) -> None:
        previous = self.position().y
        following = follow and (not self.ids or self.selected == self.ids[-1])
        self.ids = identifiers
        if not identifiers:
            self.selected = None
        elif following:
            self.selected = identifiers[-1]
        elif self.selected not in identifiers:
            self.selected = identifiers[min(previous, len(identifiers) - 1)]

    def move(self, step: int) -> None:
        if self.ids:
            index = max(0, min(self.position().y + step, len(self.ids) - 1))
            self.selected = self.ids[index]
        self.triage.changed()

    def home(self) -> None:
        self.move(-self.position().y)

    def end(self) -> None:
        self.move(len(self.ids))

    def rows(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        events = self.triage.trail.events
        for identifier in self.ids:
            event = events.get(identifier)
            if event is None:
                continue
            style = ''
            if identifier == self.selected:
                style = 'class:triage.selection'
                if self.triage.focused is self:
                    style += '.active'
            path = event.get.src_path or ''
            name = Path(path).name
            label = f'{self.triage.order[identifier]}. {Renderer.verb(event)} {name}'
            label = ' '.join(label.splitlines())
            out.append((style, label + '\n'))
        if out:
            out[-1] = (out[-1][0], out[-1][1].removesuffix('\n'))
        return out


class Inspector(FormattedTextControl):
    def __init__(self, triage: Triage) -> None:
        self.triage = triage
        self.identifier: str | None = None
        self.row = 0
        super().__init__(self.text, focusable=True, get_cursor_position=self.position)
        self.window = Window(
            self,
            wrap_lines=True,
            always_hide_cursor=True,
            right_margins=[ScrollbarMargin()],
        )
        self.header = Window(
            FormattedTextControl(' Event details'),
            height=1,
            style=self.header_style,
        )

    def header_style(self) -> str:
        out = 'class:triage.header.details'
        if self.triage.focused is self:
            out += '.active'
        return out

    def text(self) -> StyleAndTextTuples:
        event = self.triage.trail.events.get(self.triage.active.selected)
        if event is None:
            self.identifier = None
            self.row = 0
            return [('class:info', ' No event selected')]
        if event.id != self.identifier:
            self.identifier = event.id
            self.row = 0
        style = VERB_STYLES.get(Renderer.verb(event), 'class:event')
        out: StyleAndTextTuples = [(style, type(event).__name__)]
        for name, value in event._repr_items():
            out.extend([
                ('', '\n'),
                ('class:parameter', f'{INDENT}{name}: '),
                ('', fmt.bare(value)),
            ])
        return out

    def position(self) -> Point:
        out = Point(x=0, y=self.row)
        return out

    def move(self, step: int) -> None:
        lines = (
            fragment_list_to_text(self.text())
            .splitlines()
        )
        self.row = max(0, min(self.row + step, len(lines) - 1))
        self.triage.changed()

    def home(self) -> None:
        self.move(-self.row)

    def end(self) -> None:
        lines = (
            fragment_list_to_text(self.text())
            .splitlines()
        )
        self.move(len(lines))


class Triage:
    def __init__(self, trail: Trail) -> None:
        self.trail = trail
        self.decisions: dict[str, str] = {}
        self.order: dict[str, int] = {}
        self.panes = [
            Pane(self, name)
            for name in (DECLINED, PENDING, ACCEPTED)
        ]
        self.active = self.panes[1]
        self.inspector = Inspector(self)
        self.query = TextArea(
            height=1,
            prompt=' Query › ',
            multiline=False,
            wrap_lines=False,
            focus_on_click=True,
            history=InMemoryHistory(),
        )
        self.query.window.style = self.query_style
        self.query.buffer.on_text_changed += self.edited
        self.matches: set[str] | None = None
        self.expression = ''
        self.escaping = False
        self.confirming = 0
        self.message = ''
        self.sync()

    @property
    def focused(self) -> Pane | Inspector | BufferControl:
        out = self.active
        application = self.__dict__.get('application')
        if application is not None:
            out = application.layout.current_control
        return out

    def query_style(self) -> str:
        out = 'class:triage.header.query'
        if self.focused is self.query.control:
            out += '.active'
        return out

    def edited(self, buffer: Buffer) -> None:
        self.changed()

    def apply_query(self) -> None:
        expression = self.query.text.strip()
        if not expression:
            self.clear_query()
            return
        root = Path.cwd()
        if self.trail.dir is not None:
            root = self.trail.dir.parent
        candidates = self.trail.events.select('is_accepted=false')
        query = Query(candidates, root)
        try:
            selected = query(expression)
        except (TypeError, ValueError) as error:
            self.changed()
            self.message = f'Query: {error}'
            return
        self.matches = set(selected.ids)
        self.expression = expression
        self.query.buffer.append_to_history()
        self.sync()
        self.focus(self.active)
        self.message = f'Query: {len(selected)} matching events. Resubmit to include new arrivals.'

    def clear_query(self) -> None:
        self.query.text = ''
        self.expression = ''
        self.matches = None
        self.sync()
        self.focus(self.active)
        self.message = 'Query cleared.'

    def sync(self) -> None:
        groups: dict[str, list[str]] = {
            name: []
            for name in (DECLINED, PENDING, ACCEPTED)
        }
        totals = {
            name: 0
            for name in groups
        }
        for identifier in self.trail.events.ids:
            if identifier not in self.order:
                self.order[identifier] = len(self.order)
            event = self.trail.events[identifier]
            if not event.is_accepted:
                name = self.decisions.get(identifier, PENDING)
                totals[name] += 1
                if self.matches is None or identifier in self.matches:
                    groups[name].append(identifier)
        for pane in self.panes:
            pane.total = totals[pane.name]
            pane.replace(groups[pane.name], follow=pane.name == PENDING)

    def changed(self) -> None:
        self.escaping = False
        self.confirming = 0
        self.message = ''

    def navigate(self, step: int) -> None:
        index = max(0, min(self.panes.index(self.active) + step, 2))
        self.focus(self.panes[index])

    def focus(self, control: Pane | Inspector | BufferControl) -> None:
        if isinstance(control, Pane):
            self.active = control
        self.application.layout.focus(control)
        self.changed()

    def inspect(self) -> None:
        if self.focused is self.inspector:
            self.focus(self.active)
        else:
            self.focus(self.inspector)

    def scroll(
        self,
        control: Pane | Inspector,
        event: MouseEvent,
    ) -> NotImplementedType | None:
        if event.event_type == MouseEventType.SCROLL_UP:
            step = -1
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            step = 1
        else:
            return NotImplemented
        self.focus(control)
        if control is self.inspector:
            control.window.vertical_scroll = max(0, control.window.vertical_scroll + step)
        control.move(step)
        return None

    def mouse_handlers(self, application: Application) -> None:
        screen = application.renderer.last_rendered_screen
        if screen is None:
            return
        for control in [*self.panes, self.inspector]:
            handler = partial(self.scroll, control)
            for window in (control.header, control.window):
                position = screen.visible_windows_to_write_positions.get(window)
                if position is None:
                    continue
                application.renderer.mouse_handlers.set_mouse_handler_for_range(
                    x_min=position.xpos,
                    x_max=position.xpos + position.width,
                    y_min=position.ypos,
                    y_max=position.ypos + position.height,
                    handler=handler,
                )

    def page(self, step: int) -> None:
        control = self.focused
        info = control.window.render_info
        height = 1
        if info is not None:
            height = max(1, info.window_height)
            control.window.vertical_scroll = max(0, info.vertical_scroll + step * height)
        control.move(step * height)

    def decide(
        self,
        accepted: bool,
        all_events: bool = False,
    ) -> None:
        if all_events:
            identifiers = self.active.ids.copy()
        elif self.active.selected is not None:
            identifiers = [self.active.selected]
        else:
            return
        if accepted:
            destination = ACCEPTED
            if self.active.name == DECLINED:
                destination = PENDING
        else:
            destination = DECLINED
            if self.active.name == ACCEPTED:
                destination = PENDING
        for identifier in identifiers:
            if destination == PENDING:
                self.decisions.pop(identifier, None)
            else:
                self.decisions[identifier] = destination
        self.sync()
        self.changed()

    def reset(self) -> None:
        self.decisions.clear()
        self.sync()
        self.changed()
        self.message = 'Decisions reset.'

    def enter(self) -> None:
        if (
            self.focused is self.query.control
            or self.query.text.strip() != self.expression
        ):
            self.apply_query()
            return
        if not self.decisions:
            self.changed()
            self.message = 'No decisions to apply.'
            return
        self.escaping = False
        self.confirming += 1
        accepted = sum(value == ACCEPTED for value in self.decisions.values())
        declined = sum(value == DECLINED for value in self.decisions.values())
        if self.confirming == 1:
            self.message = (
                f'Apply {accepted} accepted and delete {declined}? '
                'Press Enter twice more to apply.'
            )
        elif self.confirming == 2:
            self.message = (
                f'Apply {accepted} accepted and delete {declined}? '
                'Press Enter once more to apply.'
            )
        else:
            self.confirming = 0
            self.finalize()

    def escape(self) -> None:
        if self.query.text or self.matches is not None:
            self.clear_query()
        elif self.escaping:
            self.application.exit()
        else:
            self.changed()
            self.escaping = True
            self.message = 'Discard unapplied decisions and exit? Press Esc again to exit.'

    def finalize(self) -> None:
        events = self.trail.events
        accepted = [
            identifier
            for identifier, decision in self.decisions.items()
            if decision == ACCEPTED and identifier in events
        ]
        declined = [
            identifier
            for identifier, decision in self.decisions.items()
            if decision == DECLINED and identifier in events
        ]
        try:
            if accepted or declined:
                events.finalize(accepted, declined)
        except OSError as error:
            self.message = f'Could not finalize: {error}'
            return
        self.decisions.clear()
        self.sync()
        self.escaping = False
        self.confirming = 0
        self.message = f'Finalized: {len(accepted)} accepted, {len(declined)} deleted.'

    def footer(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if self.focused is self.query.control:
            out = [
                ('class:triage.key', '[Enter]'),
                ('class:triage.help', ' Apply query  '),
                ('class:triage.key', '[Esc]'),
                ('class:triage.help', ' Clear query  '),
                ('class:triage.key', '[Tab]'),
                ('class:triage.help', ' Return to events'),
            ]
            return out
        if self.focused is self.inspector:
            movement = 'Scroll details'
            boundary = 'Top/bottom'
            focus = 'Return to events'
        else:
            movement = 'Select event'
            boundary = 'First/last event'
            focus = 'Focus details'
        escape_action = 'Confirm exit'
        if self.query.text or self.matches is not None:
            escape_action = 'Clear query'
        groups = [
            [
                ('/', 'Query'),
                ('y', 'Accept event'),
                ('n', 'Decline event'),
                ('Shift+Y', f'Accept all {len(self.active.ids)}'),
                ('Shift+N', f'Decline all {len(self.active.ids)}'),
                ('Enter ×3', 'Apply decisions'),
                ('Esc', escape_action),
                ('Esc twice', 'Exit'),
            ],
            [
                ('←/→', 'Switch pane'),
                ('↑/↓', movement),
                ('PgUp/PgDn', 'Scroll page'),
                ('Home/End', boundary),
                ('Tab', focus),
            ],
        ]
        width = self.application.output.get_size().columns
        for index, group in enumerate(groups):
            if index:
                out.append(('', '\n'))
            used = 0
            for key, action in group:
                size = get_cwidth(f'[{key}] {action}  ')
                if used and used + size > width:
                    out.append(('', '\n'))
                    used = 0
                out.extend([
                    ('class:triage.key', f'[{key}]'),
                    ('class:triage.help', f' {action}  '),
                ])
                used += size
        return out

    @cached_property
    def bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        browsing = Condition(lambda: self.focused is not self.query.control)

        @bindings.add('left', filter=browsing)
        def left(event: KeyPressEvent) -> None:
            self.navigate(-1)

        @bindings.add('right', filter=browsing)
        def right(event: KeyPressEvent) -> None:
            self.navigate(1)

        @bindings.add('up', filter=browsing)
        def up(event: KeyPressEvent) -> None:
            self.focused.move(-1)

        @bindings.add('down', filter=browsing)
        def down(event: KeyPressEvent) -> None:
            self.focused.move(1)

        @bindings.add('tab')
        @bindings.add('s-tab')
        def inspect(event: KeyPressEvent) -> None:
            if self.focused is self.query.control:
                self.focus(self.active)
            else:
                self.inspect()

        @bindings.add('/', filter=browsing)
        def query(event: KeyPressEvent) -> None:
            self.focus(self.query.control)

        @bindings.add('home', filter=browsing)
        def home(event: KeyPressEvent) -> None:
            self.focused.home()

        @bindings.add('end', filter=browsing)
        def end(event: KeyPressEvent) -> None:
            self.focused.end()

        @bindings.add('y', filter=browsing)
        def accept(event: KeyPressEvent) -> None:
            self.decide(True)

        @bindings.add('n', filter=browsing)
        def decline(event: KeyPressEvent) -> None:
            self.decide(False)

        @bindings.add('Y', filter=browsing)
        def accept_all(event: KeyPressEvent) -> None:
            self.decide(True, all_events=True)

        @bindings.add('N', filter=browsing)
        def decline_all(event: KeyPressEvent) -> None:
            self.decide(False, all_events=True)

        @bindings.add('enter')
        def finalize(event: KeyPressEvent) -> None:
            self.enter()

        @bindings.add('escape')
        def escape(event: KeyPressEvent) -> None:
            self.escape()

        @bindings.add('escape', '[', 'I')
        @bindings.add('escape', '[', 'O')
        def terminal_focus(event: KeyPressEvent) -> None:
            pass

        @bindings.add('c-c')
        @bindings.add('c-d')
        def quit(event: KeyPressEvent) -> None:
            event.app.exit()

        @bindings.add('pageup', filter=browsing)
        def pageup(event: KeyPressEvent) -> None:
            self.page(-1)

        @bindings.add('pagedown', filter=browsing)
        def pagedown(event: KeyPressEvent) -> None:
            self.page(1)

        return bindings

    @cached_property
    def application(self) -> Application:
        containers = [
            pane.container
            for pane in self.panes
        ]
        panes = VSplit(
            containers,
            padding=1,
            padding_char='│',
        )
        inspector = HSplit([
            self.inspector.header,
            self.inspector.window,
        ], height=Dimension(min=2, preferred=2, weight=1))
        top = HSplit([panes], height=Dimension(min=2, preferred=2, weight=2))
        status = Window(FormattedTextControl(lambda: self.message), height=1)
        footer = Window(
            FormattedTextControl(self.footer),
            wrap_lines=True,
            dont_extend_height=True,
        )
        container = HSplit([top, inspector, self.query, status, footer])
        layout = Layout(container, focused_element=self.active)
        out = Application(
            layout=layout,
            key_bindings=self.bindings,
            style=STYLE,
            full_screen=True,
            mouse_support=True,
            after_render=self.mouse_handlers,
            min_redraw_interval=0.05,
        )
        return out

    async def stream(self, watching: AsyncIterator[Event]) -> None:
        async for event in watching:
            if event.id in self.order:
                continue
            self.sync()
            self.application.invalidate()

    async def run(self) -> None:
        if not self.trail.lock.acquire(self):
            raise RuntimeError('Another process is recording this Trail; use its triage command')
        watching = self.trail.events.watch()
        self.sync()
        streaming = asyncio.create_task(self.stream(watching), name='trail-triage-stream')
        try:
            await self.application.run_async()
        finally:
            streaming.cancel()
            try:
                with suppress(asyncio.CancelledError):
                    await streaming
            finally:
                await watching.aclose()
                self.trail.lock.release(self)
