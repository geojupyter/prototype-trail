from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from functools import cached_property, partial
from pathlib import Path
from types import NotImplementedType

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples, fragment_list_to_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import Dimension, FormattedTextControl, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth

from trail import fmt
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
        self.escaping = False
        self.message = ''
        self.sync()

    @property
    def focused(self) -> Pane | Inspector:
        out = self.active
        application = self.__dict__.get('application')
        if application is not None:
            out = application.layout.current_control
        return out

    def sync(self) -> None:
        groups: dict[str, list[str]] = {
            name: []
            for name in (DECLINED, PENDING, ACCEPTED)
        }
        for identifier in self.trail.events.ids:
            if identifier not in self.order:
                self.order[identifier] = len(self.order)
            event = self.trail.events[identifier]
            if not event.is_accepted:
                name = self.decisions.get(identifier, PENDING)
                groups[name].append(identifier)
        for pane in self.panes:
            pane.replace(groups[pane.name], follow=pane.name == PENDING)

    def changed(self) -> None:
        self.escaping = False
        self.message = ''

    def navigate(self, step: int) -> None:
        index = max(0, min(self.panes.index(self.active) + step, 2))
        self.focus(self.panes[index])

    def focus(self, control: Pane | Inspector) -> None:
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

    def decide(self, accepted: bool) -> None:
        identifier = self.active.selected
        if identifier is None:
            return
        if accepted:
            destination = ACCEPTED
            if self.active.name == DECLINED:
                destination = PENDING
        else:
            destination = DECLINED
            if self.active.name == ACCEPTED:
                destination = PENDING
        if destination == PENDING:
            self.decisions.pop(identifier, None)
        else:
            self.decisions[identifier] = destination
        self.sync()
        self.changed()

    def reset(self) -> None:
        self.decisions.clear()
        self.sync()
        self.message = 'Choices reset. Esc again exits.'
        self.escaping = True

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
        self.message = f'Finalized: {len(accepted)} accepted, {len(declined)} deleted.'

    def footer(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if self.focused is self.inspector:
            movement = 'Scroll details'
            boundary = 'Top/bottom'
            focus = 'Return to events'
        else:
            movement = 'Select event'
            boundary = 'First/last event'
            focus = 'Focus details'
        groups = [
            [
                ('Y', 'Accept event'),
                ('N', 'Decline event'),
                ('Enter', 'Apply decisions'),
                ('Esc', 'Reset decisions'),
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

        @bindings.add('left')
        def left(event: KeyPressEvent) -> None:
            self.navigate(-1)

        @bindings.add('right')
        def right(event: KeyPressEvent) -> None:
            self.navigate(1)

        @bindings.add('up')
        def up(event: KeyPressEvent) -> None:
            self.focused.move(-1)

        @bindings.add('down')
        def down(event: KeyPressEvent) -> None:
            self.focused.move(1)

        @bindings.add('tab')
        @bindings.add('s-tab')
        def inspect(event: KeyPressEvent) -> None:
            self.inspect()

        @bindings.add('home')
        def home(event: KeyPressEvent) -> None:
            self.focused.home()

        @bindings.add('end')
        def end(event: KeyPressEvent) -> None:
            self.focused.end()

        @bindings.add('y')
        @bindings.add('Y')
        def accept(event: KeyPressEvent) -> None:
            self.decide(True)

        @bindings.add('n')
        @bindings.add('N')
        def decline(event: KeyPressEvent) -> None:
            self.decide(False)

        @bindings.add('enter')
        def finalize(event: KeyPressEvent) -> None:
            self.finalize()

        @bindings.add('escape')
        def escape(event: KeyPressEvent) -> None:
            if self.escaping:
                event.app.exit()
            else:
                self.reset()

        @bindings.add('escape', '[', 'I')
        @bindings.add('escape', '[', 'O')
        def terminal_focus(event: KeyPressEvent) -> None:
            pass

        @bindings.add('c-c')
        @bindings.add('c-d')
        def quit(event: KeyPressEvent) -> None:
            event.app.exit()

        @bindings.add('pageup')
        def pageup(event: KeyPressEvent) -> None:
            self.page(-1)

        @bindings.add('pagedown')
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
        container = HSplit([top, inspector, status, footer])
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
