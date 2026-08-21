"""Ubuntu desktop capabilities."""

from __future__ import annotations

import asyncio
import shlex

from .controls import Guest
from .debian import DebianAdapter, GuestControlError


class UbuntuAdapter(DebianAdapter):
    """Ubuntu capabilities layered on Debian's command and network support."""

    def __init__(self, guest: Guest, *, sudo_password: str | None = None) -> None:
        super().__init__(guest, sudo_password=sudo_password)
        self.window = UbuntuWindow(self)


class UbuntuWindow:
    """GNOME window-manager actions available on Ubuntu Desktop."""

    def __init__(self, adapter: UbuntuAdapter) -> None:
        self._adapter = adapter

    async def activate(self, title: str) -> None:
        """Bring one visible X11 window with ``title`` to the foreground."""

        if not title:
            raise ValueError("window title must not be empty")
        command = (
            "xdotool search --onlyvisible --name "
            f"{shlex.quote(title)} windowactivate --sync %@"
        )
        await self._run_x11_action(command)

    async def minimize(self) -> None:
        """Hide the focused X11 window through Ubuntu's window manager."""

        # GNOME's Super+H and Alt+F9 shortcuts are not routed reliably by the
        # live image's Xwayland RFB path.  Match GNOME Terminal's title only;
        # a broad window search could minimize the application under test.
        await self._adapter.terminal.run(
            "xdotool search --onlyvisible --name ubuntu@ubuntu "
            "windowminimize %@"
        )
        self._adapter.terminal._mark_unfocused()

    async def click(self, *, x: int, y: int) -> None:
        """Click the focused X11 surface at framebuffer coordinates."""

        await self._pointer(x=x, y=y, button=1)

    async def right_click(self, *, x: int, y: int) -> None:
        """Open a context menu on the focused X11 surface."""

        await self._pointer(x=x, y=y, button=3)

    async def context_paste(self, *, x: int, y: int) -> None:
        """Paste the X11 clipboard into a focused control through its menu.

        SWT fields in the Ubuntu live session can ignore an RFB text event.
        Sending the right click and the menu mnemonic in one X11 action keeps
        the context menu alive; reopening the helper terminal between those
        two events can move focus back to the terminal.
        """

        if x < 0 or y < 0:
            raise ValueError("window coordinates must not be negative")
        await self._run_x11_action(
            f"xdotool mousemove {x} {y} click 3; sleep 0.2; xdotool key p"
        )

    async def move(self, *, x: int, y: int) -> None:
        """Move the pointer over the focused X11 surface."""

        await self._pointer(x=x, y=y, button=None)

    async def press(self, key: str) -> None:
        """Send one named key through the active X11 application."""

        if not key or any(character.isspace() for character in key):
            raise ValueError("X11 key names must not be empty or contain whitespace")
        await self._run_x11_action("xdotool key " + shlex.quote(key))

    async def _pointer(self, *, x: int, y: int, button: int | None) -> None:
        """Use X11 input when an Xwayland application ignores RFB input."""

        if x < 0 or y < 0:
            raise ValueError("window coordinates must not be negative")
        action = f"xdotool mousemove {x} {y}"
        if button is not None:
            action += f" click {button}"
        await self._run_x11_action(action)

    async def _run_x11_action(self, action: str, *, minimize_terminal: bool = True) -> None:
        """Run one native X11 action, then return control to the desktop."""

        for attempt in range(3):
            try:
                await self._adapter.terminal.focus()
                break
            except GuestControlError:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
        if minimize_terminal:
            # The minimized-terminal transition is asynchronous in GNOME. A
            # native event sent in the same compositor tick can still target
            # the terminal instead of the visible control behind it, so give
            # the window manager one short turn before emitting the action.
            action = "xdotool getactivewindow windowminimize && sleep 1 && " + action
        await self._adapter.terminal.run(action, timeout=30)
        self._adapter.terminal._mark_unfocused()
