from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from trail.node import Node as Base

if TYPE_CHECKING:
    from trail.cli.console import Console
    from trail.cli.feed import Feed
    from trail.cli.render import Renderer


class Node(Base):
    """
    A Node inside the console. It finds the console's parts by walking up its parents, like
    the base Node does for the Trail.
    """

    _parent: Node

    @cached_property
    def _console(self) -> Console:
        """The console this node belongs to."""
        from trail.cli.console import Console

        parent = self._parent
        if isinstance(parent, Console):
            return parent
        return parent._console

    @cached_property
    def _feed(self) -> Feed:
        """The console's feed, where a command's output goes."""
        from trail.cli.console import Console

        parent = self._parent
        if isinstance(parent, Console):
            return parent.feed
        return parent._feed

    @cached_property
    def _renderer(self) -> Renderer:
        """The console's renderer, used to format what a node prints."""
        from trail.cli.console import Console
        from trail.cli.render import Renderer

        parent = self._parent
        if isinstance(parent, Renderer):
            return parent
        if isinstance(parent, Console):
            return parent.renderer
        return parent._renderer
