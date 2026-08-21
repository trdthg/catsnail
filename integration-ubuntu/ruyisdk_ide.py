"""RuyiSDK IDE integration scenario on Ubuntu 24.04 Desktop.

Run explicitly because the IDE archive is about 400 MiB:

    uv run catsnail run integration-ubuntu/ruyisdk_ide.py

The installation test is a durable checkpoint. Later IDE scenarios restore it
instead of downloading and installing the IDE again.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

from catsnail import (
    Guest,
    Machine,
    NetUser,
    UbuntuAdapter,
    add_net,
    add_os,
    add_test,
    use,
    xfail,
)
from catsnail.guest import GuestControlError


UBUNTU_INSTALLER_LANGUAGE = (
    Path(__file__).with_name("assets") / "ubuntu-installer-language.png"
)
UBUNTU_INSTALLER_PREPARING = (
    Path(__file__).with_name("assets") / "ubuntu-installer-preparing.png"
)
UBUNTU_DESKTOP_DOCK = Path(__file__).with_name("assets") / "ubuntu-desktop-dock.png"
RUYISDK_INSTALLATION_REQUIRED = (
    Path(__file__).with_name("assets") / "ruyisdk-installation-required.png"
)
RUYI_INSTALLATION_WELCOME = (
    Path(__file__).with_name("assets") / "ruyi-installation-welcome-ubuntu.png"
)
RUYI_INSTALLATION_CONFIGURATION = (
    Path(__file__).with_name("assets") / "ruyi-installation-configuration-ubuntu.png"
)
RUYI_INSTALLATION_COMPLETE = (
    Path(__file__).with_name("assets") / "ruyi-installation-complete-ubuntu.png"
)
RUYI_INSTALLATION_FAILED = (
    Path(__file__).with_name("assets") / "ruyi-installation-failed-ubuntu.png"
)
RUYISDK_QUESTIONNAIRE = (
    Path(__file__).with_name("assets") / "ruyisdk-questionnaire-ubuntu.png"
)
RUYISDK_IDE_SHELL = Path(__file__).with_name("assets") / "ruyisdk-ide-shell-ubuntu.png"
RUYISDK_MENU = Path(__file__).with_name("assets") / "ruyisdk-menu.png"
RUYISDK_VIEWS_MENU = Path(__file__).with_name("assets") / "ruyisdk-views-menu.png"
RUYISDK_NEW_WIZARD = Path(__file__).with_name("assets") / "ruyisdk-new-wizard.png"
RUYISDK_PROJECT_SELECTED = (
    Path(__file__).with_name("assets") / "ruyisdk-project-selected.png"
)
RUYISDK_BOARD_MODEL = Path(__file__).with_name("assets") / "ruyisdk-board-model.png"
RUYISDK_BOARD_MODEL_SELECTED = (
    Path(__file__).with_name("assets") / "ruyisdk-board-model-selected.png"
)
RUYISDK_PROJECT_SETTINGS = (
    Path(__file__).with_name("assets") / "ruyisdk-project-settings.png"
)
RUYISDK_PROJECT_SETTINGS_FILLED = (
    Path(__file__).with_name("assets") / "ruyisdk-project-settings-filled.png"
)
RUYISDK_PROJECT_TREE = Path(__file__).with_name("assets") / "ruyisdk-project-tree.png"
RUYISDK_WORKBENCH_LAYOUT = (
    Path(__file__).with_name("assets") / "ruyisdk-workbench-layout.png"
)
RUYISDK_PREFERENCES_DIALOG = (
    Path(__file__).with_name("assets") / "ruyisdk-preferences-dialog.png"
)
RUYISDK_PREFERENCES_GENERAL_EXPANDED = (
    Path(__file__).with_name("assets") / "ruyisdk-preferences-general-expanded.png"
)
RUYISDK_PREFERENCE_PROMPT = (
    Path(__file__).with_name("assets") / "ruyisdk-preference-prompt.png"
)
RUYISDK_PREFERENCE_NEVER_OPEN = (
    Path(__file__).with_name("assets") / "ruyisdk-preference-never-open.png"
)
RUYISDK_RESET_PERSPECTIVE_MENU = (
    Path(__file__).with_name("assets") / "ruyisdk-reset-perspective-menu.png"
)
RUYISDK_RESET_PERSPECTIVE_CONFIRMATION = (
    Path(__file__).with_name("assets") / "ruyisdk-reset-perspective-confirmation.png"
)
RUYISDK_FILE_RESTART_MENU = (
    Path(__file__).with_name("assets") / "ruyisdk-file-restart-menu.png"
)
RUYISDK_DEFAULT_PACKAGE_LAYOUT = (
    Path(__file__).with_name("assets") / "ruyisdk-default-package-layout.png"
)
RUYISDK_DEFAULT_VENV_LAYOUT = (
    Path(__file__).with_name("assets") / "ruyisdk-default-venv-layout.png"
)
RUYISDK_DEFAULT_WEBSITE_LAYOUT = (
    Path(__file__).with_name("assets") / "ruyisdk-default-website-layout.png"
)
RUYISDK_DEFAULT_NEWS_LAYOUT = (
    Path(__file__).with_name("assets") / "ruyisdk-default-news-layout.png"
)
RUYISDK_CURRENT_DEVICE_NONE = (
    Path(__file__).with_name("assets") / "ruyisdk-current-device-none.png"
)
RUYISDK_PACKAGE_INDEX_UPDATED = (
    Path(__file__).with_name("assets") / "ruyisdk-package-index-updated.png"
)
RUYISDK_DEVICE_SELECTOR = (
    Path(__file__).with_name("assets") / "ruyisdk-device-selector.png"
)
RUYISDK_DEVICE_LIST_LOADED = (
    Path(__file__).with_name("assets") / "ruyisdk-device-list-loaded.png"
)
RUYISDK_DEVICE_MILK_V_DUO_SELECTED = (
    Path(__file__).with_name("assets") / "ruyisdk-device-milk-v-duo-selected.png"
)
RUYISDK_DEVICE_SORT_NAME = (
    Path(__file__).with_name("assets") / "ruyisdk-device-sort-name.png"
)
RUYISDK_DEVICE_SORT_ID = (
    Path(__file__).with_name("assets") / "ruyisdk-device-sort-id.png"
)
RUYISDK_PACKAGE_FILTERED = (
    Path(__file__).with_name("assets") / "ruyisdk-device-filtered.png"
)
RUYISDK_PACKAGE_EXPLORER_ACTIONS = (
    Path(__file__).with_name("assets") / "ruyisdk-package-explorer-actions.png"
)
RUYISDK_PACKAGE_EXPLORER_MAXIMIZED = (
    Path(__file__).with_name("assets") / "ruyisdk-package-explorer-maximized.png"
)
RUYISDK_PACKAGE_GNU_UPSTREAM_SELECTED = (
    Path(__file__).with_name("assets") / "ruyisdk-package-gnu-upstream-selected.png"
)
RUYISDK_PACKAGE_GNU_UPSTREAM_LATEST_CHECKED = (
    Path(__file__).with_name("assets")
    / "ruyisdk-package-gnu-upstream-latest-checked.png"
)
RUYISDK_GNU_UPSTREAM_LATEST_INSTALLED = (
    Path(__file__).with_name("assets") / "ruyisdk-package-installed-marker.png"
)
RUYISDK_GNU_UPSTREAM_LATEST_UNSELECTED = (
    Path(__file__).with_name("assets") / "ruyisdk-gnu-upstream-latest-unselected.png"
)
RUYISDK_GNU_UPSTREAM_LATEST_REMOVED = (
    Path(__file__).with_name("assets") / "ruyisdk-gnu-upstream-latest-removed.png"
)
RUYISDK_PACKAGE_CONFIRM_CHANGES = (
    Path(__file__).with_name("assets") / "ruyisdk-package-confirm-changes.png"
)
RUYISDK_PACKAGE_OPERATION_COMPLETE = (
    Path(__file__).with_name("assets") / "ruyisdk-package-operation-complete.png"
)
RUYISDK_VENV_CONFIGURATION = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-configuration.png"
)
RUYISDK_VENV_OPEN = Path(__file__).with_name("assets") / "ruyisdk-venv-open.png"
RUYISDK_VENV_EMPTY = Path(__file__).with_name("assets") / "ruyisdk-venv-empty.png"
RUYISDK_VENV_INDEX_UPDATED = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-index-updated.png"
)
RUYISDK_VENV_MAXIMIZED = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-maximized.png"
)
RUYISDK_VENV_PROFILE_NAME = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-profile-name.png"
)
RUYISDK_VENV_PROFILE_QUIRKS = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-profile-quirks.png"
)
RUYISDK_VENV_DEFAULT_FINISH_DISABLED = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-default-finish-disabled.png"
)
RUYISDK_NEWS_OPEN = Path(__file__).with_name("assets") / "ruyisdk-news-open.png"
RUYISDK_NEWS_NARROW = Path(__file__).with_name("assets") / "ruyisdk-news-narrow.png"
RUYISDK_NEWS_NARROW_LIST = (
    Path(__file__).with_name("assets") / "ruyisdk-news-narrow-list.png"
)
RUYISDK_NEWS_LIST_LOADED = (
    Path(__file__).with_name("assets") / "ruyisdk-news-list-loaded.png"
)
RUYISDK_NEWS_UNREAD = Path(__file__).with_name("assets") / "ruyisdk-news-unread.png"
RUYISDK_NEWS_READ = Path(__file__).with_name("assets") / "ruyisdk-news-read.png"
RUYISDK_NEWS_UNREAD_RESULTS = (
    Path(__file__).with_name("assets") / "ruyisdk-news-unread-results.png"
)
RUYISDK_NEWS_DETAIL_040 = (
    Path(__file__).with_name("assets") / "ruyisdk-news-detail-0.40.png"
)
RUYISDK_NEWS_SEARCH_040 = (
    Path(__file__).with_name("assets") / "ruyisdk-news-search-040.png"
)
RUYISDK_CDT_PROJECT_WIZARD = (
    Path(__file__).with_name("assets") / "ruyisdk-cdt-project-wizard.png"
)
RUYISDK_CDT_PROJECT_SELECTED = (
    Path(__file__).with_name("assets") / "ruyisdk-cdt-project-selected.png"
)
RUYISDK_CDT_SELECT_CONFIGURATIONS = (
    Path(__file__).with_name("assets") / "ruyisdk-cdt-select-configurations.png"
)
RUYISDK_CDT_TOOLCHAIN = Path(__file__).with_name("assets") / "ruyisdk-cdt-toolchain.png"
RUYISDK_CDT_PROJECT_TREE = (
    Path(__file__).with_name("assets") / "ruyisdk-cdt-project-tree.png"
)
RUYISDK_CDT_PROJECT_TREE_RUYI_PERSPECTIVE = (
    Path(__file__).with_name("assets") / "ruyisdk-cdt-project-name.png"
)
RUYISDK_VENV_PROJECT_SELECTED = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-project-selected.png"
)
RUYISDK_VENV_LOCATION_SUMMARY = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-location-summary.png"
)
RUYISDK_VENV_LOCATION_SUMMARY_STABLE = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-location-summary-stable.png"
)
RUYISDK_VENV_GNU_UPSTREAM_SELECTED = (
    Path(__file__).with_name("assets")
    / "ruyisdk-venv-gnu-upstream-selected-stable.png"
)
RUYISDK_VENV_VERSION_SELECTED = (
    Path(__file__).with_name("assets")
    / "ruyisdk-venv-version-selected-stable.png"
)
RUYISDK_VENV_NO_SYSROOT = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-no-sysroot.png"
)
RUYISDK_VENV_SYSROOT_SELECTABLE = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-sysroot-selectable.png"
)
RUYISDK_VENV_CREATED_PROJECT = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-created-project.png"
)
RUYISDK_VENV_INFO_PROJECT = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-info-project.png"
)
RUYISDK_VENV_INFO_PROFILE = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-info-profile.png"
)
RUYISDK_VENV_APPLIED = Path(__file__).with_name("assets") / "ruyisdk-venv-applied.png"
RUYISDK_PROJECT_CONTEXT = (
    Path(__file__).with_name("assets") / "ruyisdk-project-context.png"
)
RUYISDK_VENV_DELETE_CONFIRMATION = (
    Path(__file__).with_name("assets") / "ruyisdk-venv-delete-confirmation.png"
)
RUYISDK_PROJECT_VENV_DIRECTORY_REMOVED = (
    Path(__file__).with_name("assets") / "ruyisdk-project-venv-directory-removed.png"
)

UBUNTU_DESKTOP_URL = (
    "https://releases.ubuntu.com/24.04/ubuntu-24.04.4-desktop-amd64.iso"
)
UBUNTU_DESKTOP_SHA256 = (
    "3a4c9877b483ab46d7c3fbe165a0db275e1ae3cfe56a5657e5a47c2f99a99d1e"
)

RUYISDK_VERSION = "0.0.3"
PROJECT_NAME = "demo"
CDT_PROJECT_NAME = "venv-demo"
RUYISDK_URL = (
    "https://fast-mirror.isrc.ac.cn/ruyisdk/ide/0.0.3/"
    "ruyisdk-0.0.3-linux.gtk.x86_64.tar.gz"
)
# The documented mirror does not publish a SHA256 sidecar. Its versioned index
# reports this exact x86_64 archive size; the plugin archive below is SHA256-pinned.
RUYISDK_ARCHIVE_SIZE = 419_601_008
RUYISDK_SHA256 = "8f60cae355916e10ee3926c39158e384e90a8307580065b7992bd3ea0452ac94"

PLUGIN_VERSION = "0.1.6-beta.1"
PLUGIN_URL = (
    "https://github.com/ruyisdk/ruyisdk-eclipse-plugins/releases/download/"
    "v0.1.6-beta.1/ruyisdk-eclipse-plugins-0.1.6-beta.1.zip"
)
PLUGIN_SHA256 = "ad476b434c93810dc143a6521f642ed9599a5fbe24900944bb4173b130683461"

RUYISDK_EGRESS = add_net(NetUser())
DESKTOP = add_os(
    Machine(
        iso=UBUNTU_DESKTOP_URL,
        sha256=UBUNTU_DESKTOP_SHA256,
        disk_size="20G",
        memory="4G",
        vcpus=16,
        networks=(RUYISDK_EGRESS,),
    )
)


def _download_command(url: str, destination: str) -> str:
    script = (
        "from urllib.request import urlretrieve;urlretrieve("
        f"{json.dumps(url)},{json.dumps(destination)})"
    )
    return f"python3 -c {shlex.quote(script)}"


async def _type_into(desktop: Guest, value: str, *, x: int, y: int) -> None:
    """Use the normal click-and-type path for an SWT text control."""

    await desktop.screen.click(x, y)
    await desktop.keyboard.type(value)


async def _show_workbench(ubuntu: UbuntuAdapter, *, label: str) -> None:
    """Activate the RuyiSDK workbench after terminal-driven checks."""

    await ubuntu.window.activate("RuyiSDK IDE")
    await ubuntu.guest.screen.move(500, 700)
    await ubuntu.guest.screen.assert_screen(
        RUYISDK_IDE_SHELL,
        x=66,
        y=68,
        timeout=60,
        label=label,
    )


async def _click_until_screen(
    desktop: Guest,
    *,
    x: int,
    y: int,
    expected: Path,
    expected_x: int | None,
    expected_y: int | None,
    label: str,
    timeout: float = 30,
    attempts: int = 3,
    maximum_mean_difference: float = 12.0,
    move_after: tuple[int, int] | None = None,
) -> None:
    """Retry a pointer action until its expected screen is visible."""

    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        await desktop.screen.click(x, y)
        if move_after is not None:
            await desktop.screen.move(*move_after)
        try:
            await desktop.screen.assert_screen(
                expected,
                x=expected_x,
                y=expected_y,
                maximum_mean_difference=maximum_mean_difference,
                timeout=timeout if attempt == attempts - 1 else min(timeout, 15),
                label=label,
            )
        except GuestControlError:
            if attempt == attempts - 1:
                raise
        else:
            return


async def _open_device_selector(desktop: Guest) -> None:
    """Open the device chooser from the initialized Package Explorer view."""

    await desktop.screen.assert_screen(
        RUYISDK_CURRENT_DEVICE_NONE,
        x=66,
        y=202,
        timeout=30,
        label="包管理器未选择开发板",
    )
    await desktop.screen.click(180, 214)
    await desktop.screen.assert_screen(
        RUYISDK_DEVICE_SELECTOR,
        x=355,
        y=120,
        timeout=60,
        label="开发板选择器",
    )
    await desktop.screen.click(580, 554)
    await desktop.screen.assert_screen(
        RUYISDK_DEVICE_LIST_LOADED,
        x=355,
        y=120,
        timeout=600,
        label="开发板列表已加载",
    )


async def _initialize_package_explorer(desktop: Guest) -> None:
    """Refresh the package index and reopen the populated Package Explorer."""

    # Resetting an Eclipse perspective restores the Package Explorer tab but
    # does not initialize its contents. The plug-in's documented index action
    # followed by reopening the view populates its device link and package tree.
    await desktop.screen.click(528, 81)
    await desktop.screen.assert_screen(
        RUYISDK_MENU,
        x=490,
        y=97,
        timeout=30,
        label="RuyiSDK菜单已打开",
    )
    await desktop.screen.click(578, 134)
    await desktop.screen.assert_screen(
        RUYISDK_PACKAGE_INDEX_UPDATED,
        x=368,
        y=295,
        timeout=300,
        label="包管理器索引已更新",
    )
    before_dismissing_index_result = await desktop.screen.snapshot()
    await desktop.screen.click(910, 448)
    await desktop.screen.wait_for_change(
        before_dismissing_index_result,
        timeout=30,
    )
    await desktop.screen.click(528, 81)
    await desktop.screen.assert_screen(
        RUYISDK_MENU,
        x=490,
        y=97,
        timeout=30,
        label="RuyiSDK菜单已重新打开",
    )
    await desktop.screen.move(682, 160)
    await desktop.screen.assert_screen(
        RUYISDK_VIEWS_MENU,
        x=693,
        y=148,
        timeout=30,
        label="RuyiSDK视图菜单已打开",
    )
    await desktop.screen.click(782, 212)
    await desktop.screen.assert_screen(
        RUYISDK_CURRENT_DEVICE_NONE,
        x=66,
        y=202,
        timeout=60,
        label="包管理器未选择开发板",
    )


async def _select_milk_v_duo(desktop: Guest) -> None:
    """Select Milk-V Duo only after SWT has applied the list selection."""

    await _open_device_selector(desktop)
    # The remote list can finish painting while it processes the first click,
    # so keep the normal VNC pointer action coupled to its visible result.
    await asyncio.sleep(0.8)
    await _click_until_screen(
        desktop,
        x=420,
        y=287,
        expected=RUYISDK_DEVICE_MILK_V_DUO_SELECTED,
        expected_x=355,
        expected_y=274,
        label="已选择Milk-V Duo",
        attempts=5,
    )
    await _click_until_screen(
        desktop,
        x=929,
        y=554,
        expected=RUYISDK_PACKAGE_FILTERED,
        expected_x=70,
        expected_y=198,
        label="按开发板筛选的软件包",
        attempts=4,
        timeout=60,
    )


async def _select_latest_gnu_upstream(
    desktop: Guest, *, select_device: bool = True
) -> None:
    """Select GNU upstream's latest version in Package Explorer."""

    if select_device:
        await _select_milk_v_duo(desktop)
    # The view tab owns the close action.  Ctrl+F4 is handled by Eclipse as a
    # perspective shortcut in this session and leaves the lower panel open,
    # which changes the Package Explorer's row coordinates.
    before_closing_venv = await desktop.screen.snapshot()
    await desktop.screen.click(166, 486)
    await desktop.screen.wait_for_change(before_closing_venv, timeout=30)
    # Package-index updates change the number and ordering of entries below
    # gnu-upstream. It remains visible in the populated list, so select the
    # named row directly and retry if SWT drops a pointer event.
    await _maximize_package_explorer(desktop)
    await _click_until_screen(
        desktop,
        x=250,
        y=416,
        expected=RUYISDK_PACKAGE_GNU_UPSTREAM_SELECTED,
        expected_x=145,
        expected_y=405,
        label="GNU upstream软件包已选择",
        attempts=5,
        maximum_mean_difference=2.0,
        move_after=(500, 700),
    )
    await desktop.keyboard.press("RIGHT")
    await _click_until_screen(
        desktop,
        x=172,
        y=441,
        expected=RUYISDK_PACKAGE_GNU_UPSTREAM_LATEST_CHECKED,
        expected_x=164,
        expected_y=429,
        label="GNU upstream最新版本已勾选",
        attempts=4,
        maximum_mean_difference=2.0,
        timeout=30,
        move_after=(500, 700),
    )


async def _maximize_package_explorer(desktop: Guest) -> None:
    """Expose Package Explorer's action bar at stable full-width coordinates."""

    # The Package Explorer is initially docked in a narrow side pane. Its
    # maximize button is at (348, 180); after a VNC pointer move the SWT view
    # occasionally needs a second click before it applies the layout change.
    await _click_until_screen(
        desktop,
        x=348,
        y=180,
        expected=RUYISDK_PACKAGE_EXPLORER_MAXIMIZED,
        expected_x=1065,
        expected_y=204,
        label="包管理器已最大化",
        attempts=3,
        timeout=30,
        maximum_mean_difference=2.0,
    )


async def _open_package_changes(desktop: Guest, *, label: str) -> None:
    """Open the Package Explorer confirmation dialog for checked changes."""

    await _click_until_screen(
        desktop,
        x=1150,
        y=214,
        expected=RUYISDK_PACKAGE_CONFIRM_CHANGES,
        expected_x=600,
        expected_y=144,
        label=f"确认{label}",
        attempts=3,
        timeout=30,
        maximum_mean_difference=2.0,
    )
    await desktop.screen.move(500, 700)


async def _apply_package_changes(
    desktop: Guest,
    result: Path,
    result_x: int | None,
    result_y: int | None,
    *,
    label: str,
) -> None:
    """Apply checked changes, then verify the updated package row."""

    await _open_package_changes(desktop, label=label)
    await desktop.screen.click(869, 522)
    await desktop.screen.assert_screen(
        RUYISDK_PACKAGE_OPERATION_COMPLETE,
        x=380,
        y=None,
        maximum_mean_difference=2.0,
        timeout=600,
        label=f"{label}已完成",
    )
    await desktop.screen.click(903, 522)
    await desktop.screen.assert_screen(
        result,
        x=result_x,
        y=result_y,
        maximum_mean_difference=2.0,
        timeout=30,
        label=f"{label}状态已更新",
    )


async def _show_venv_configuration(desktop: Guest) -> None:
    """Maximize Ruyi Venv and open its configuration wizard."""

    await _show_ruyi_venv(desktop)
    # The workbench-maximized view keeps the documented action in its usual
    # bottom-right position. The first RFB click may only focus the XWayland
    # workbench, so retry the real action rather than changing window state.
    await _click_until_screen(
        desktop,
        x=1148,
        y=741,
        expected=RUYISDK_VENV_CONFIGURATION,
        expected_x=75,
        expected_y=76,
        label="RuyiVenv配置页",
        attempts=3,
        timeout=30,
        maximum_mean_difference=2.0,
    )


async def _wait_for_venv_index(desktop: Guest) -> None:
    """Accept an already-cached index or acknowledge its first update."""

    try:
        await desktop.screen.assert_screen(
            RUYISDK_VENV_PROFILE_NAME,
            x=76,
            y=235,
            timeout=10,
            label="RuyiVenv索引已就绪",
        )
    except GuestControlError:
        await desktop.screen.assert_screen(
            RUYISDK_VENV_INDEX_UPDATED,
            x=430,
            y=348,
            timeout=120,
            label="RuyiVenv索引已更新",
        )
        await desktop.keyboard.press("ENTER")
        await desktop.screen.assert_screen(
            RUYISDK_VENV_PROFILE_NAME,
            x=76,
            y=235,
            timeout=30,
            label="RuyiVenv索引已就绪",
        )


async def _open_venv_configuration(desktop: Guest) -> None:
    """Open Ruyi Venv after its initial package-index update completes."""

    await _show_venv_configuration(desktop)
    await _wait_for_venv_index(desktop)


async def _open_ruyi_news(desktop: Guest) -> None:
    """Open the Ruyi News view from the RuyiSDK menu."""

    for attempt in range(4):
        if attempt:
            await desktop.keyboard.press("ESC")
            await asyncio.sleep(0.5)
        await desktop.screen.click(528, 81)
        try:
            await desktop.screen.assert_screen(
                RUYISDK_MENU,
                x=490,
                y=97,
                timeout=10 if attempt < 3 else 30,
                label="RuyiSDK菜单已打开",
            )
        except GuestControlError:
            if attempt == 3:
                raise
        else:
            break
    # SWT expands this cascading entry on pointer hover. Re-activating the
    # workbench for an X11 click would collapse its already-open parent menu.
    await desktop.screen.move(682, 160)
    await desktop.screen.assert_screen(
        Path(__file__).with_name("assets") / "ruyisdk-views-menu.png",
        x=693,
        y=148,
        timeout=30,
        label="RuyiSDK视图菜单已打开",
    )
    await desktop.screen.click(752, 238)
    try:
        # Reopening an already-created view focuses its central tab. A fresh
        # workbench creates the narrow sidebar first, so accept both real UI
        # flows and converge on the same full-list assertion below.
        await desktop.screen.assert_screen(
            RUYISDK_NEWS_OPEN,
            x=378,
            y=168,
            timeout=5,
            label="RuyiNews已打开",
        )
    except GuestControlError:
        await desktop.screen.assert_screen(
            RUYISDK_NEWS_NARROW,
            # Eclipse redraws the view tab and table columns between sessions.
            # The search control is an invariant of the actual sidebar.
            x=1123,
            y=208,
            timeout=60,
            label="RuyiNews已在侧栏打开",
        )
        await desktop.screen.assert_screen(
            RUYISDK_NEWS_NARROW_LIST,
            x=1089,
            y=291,
            maximum_mean_difference=4.0,
            timeout=60,
            label="RuyiNews侧栏列表已加载",
        )
        await desktop.screen.click(1260, 180)
        await desktop.screen.assert_screen(
            RUYISDK_NEWS_OPEN,
            x=378,
            y=168,
            timeout=60,
            label="RuyiNews已打开",
        )
    await desktop.screen.assert_screen(
        RUYISDK_NEWS_LIST_LOADED,
        x=111,
        y=258,
        maximum_mean_difference=4.0,
        timeout=60,
        label="RuyiNews列表已加载",
    )


async def _open_ruyi_venv(desktop: Guest) -> None:
    """Open the Ruyi Venv view from the RuyiSDK menu."""

    try:
        await desktop.screen.assert_screen(
            RUYISDK_VENV_MAXIMIZED,
            x=70,
            y=86,
            timeout=3,
            label="RuyiVenv已打开",
        )
    except GuestControlError:
        pass
    else:
        return

    # The documented RuyiSDK layout already contains this view. Reuse that
    # visible state instead of opening Quick Access over it, which can leave
    # the command palette focused while a weak title-only assertion passes.
    try:
        await desktop.screen.assert_screen(
            RUYISDK_VENV_OPEN,
            x=70,
            y=476,
            timeout=3,
            label="RuyiVenv已打开",
        )
    except GuestControlError:
        pass
    else:
        return

    # The C/C++ editor can leave the plug-in menu in its compact, empty SWT
    # state. Returning to the Ruyi package view activates the plug-in context.
    await desktop.screen.click(250, 180)
    menu_open = False
    for attempt in range(3):
        if attempt:
            await desktop.keyboard.press("ESC")
            await asyncio.sleep(1)
        if attempt == 0:
            await desktop.screen.click(528, 81)
        elif attempt == 1:
            await desktop.keyboard.press("F10")
            for _ in range(7):
                await desktop.keyboard.press("RIGHT")
            await desktop.keyboard.press("ENTER")
        else:
            await desktop.screen.click(528, 81)
        await desktop.screen.move(500, 120)
        try:
            await desktop.screen.assert_screen(
                RUYISDK_MENU,
                x=490,
                y=97,
                timeout=15,
                label="RuyiSDK菜单已打开",
            )
        except GuestControlError:
            if attempt == 2:
                break
        else:
            menu_open = True
            break
    if not menu_open:
        # Eclipse's standard Quick Access is a normal GUI path to a view and
        # remains available when this perspective contributes an empty menu.
        await desktop.keyboard.shortcut("CTRL", "3")
        await desktop.keyboard.type("Ruyi Venv")
        await desktop.keyboard.press("ENTER")
        await desktop.screen.assert_screen(
            RUYISDK_VENV_OPEN,
            x=70,
            y=476,
            timeout=60,
            label="RuyiVenv已打开",
        )
        return
    await desktop.screen.move(682, 160)
    await desktop.screen.assert_screen(
        Path(__file__).with_name("assets") / "ruyisdk-views-menu.png",
        x=693,
        y=148,
        timeout=30,
        label="RuyiSDK视图菜单已打开",
    )
    await desktop.screen.click(800, 160)
    await desktop.screen.assert_screen(
        RUYISDK_VENV_OPEN,
        x=70,
        y=476,
        timeout=60,
        label="RuyiVenv已打开",
    )


async def _show_ruyi_venv(desktop: Guest) -> None:
    """Open and maximize Ruyi Venv for its complete table and controls."""

    await _open_ruyi_venv(desktop)
    await _assert_venv_maximized(desktop)


async def _assert_venv_maximized(desktop: Guest) -> None:
    """Ensure the Ruyi Venv view is maximized after a modal wizard closes."""

    # This is Eclipse's Maximize control, not the GNOME window control. A
    # detached Ruyi Venv view does not dispatch its New action consistently.
    await _click_until_screen(
        desktop,
        x=348,
        y=486,
        expected=RUYISDK_VENV_MAXIMIZED,
        expected_x=100,
        expected_y=170,
        maximum_mean_difference=2.0,
        timeout=30,
        label="RuyiVenv已最大化",
    )


async def _keep_ruyi_perspective_for_new_projects(desktop: Guest) -> None:
    """Keep the RuyiSDK workbench active when CDT creates its project."""

    # CDT normally switches to its C/C++ perspective after Finish. In this
    # plug-in release that perspective does not contribute the RuyiSDK menu,
    # even though the project itself is created successfully. Eclipse exposes
    # a user-facing per-workspace preference for retaining the current view.
    for attempt in range(4):
        await desktop.screen.click(634, 80)
        await desktop.screen.click(670, 266)
        try:
            await desktop.screen.assert_screen(
                RUYISDK_PREFERENCES_DIALOG,
                x=354,
                y=41,
                timeout=30 if attempt == 3 else 10,
                label="Eclipse偏好设置已打开",
            )
        except GuestControlError:
            if attempt == 3:
                raise
        else:
            break
    await _click_until_screen(
        desktop,
        x=371,
        y=133,
        expected=RUYISDK_PREFERENCES_GENERAL_EXPANDED,
        expected_x=415,
        expected_y=360,
        label="General偏好设置已展开",
        attempts=4,
        maximum_mean_difference=4.0,
    )
    await _click_until_screen(
        desktop,
        x=456,
        y=372,
        expected=RUYISDK_PREFERENCE_PROMPT,
        expected_x=552,
        expected_y=182,
        label="新建项目Perspective偏好设置",
        attempts=4,
        maximum_mean_difference=4.0,
    )
    await _click_until_screen(
        desktop,
        x=711,
        y=220,
        expected=RUYISDK_PREFERENCE_NEVER_OPEN,
        expected_x=552,
        expected_y=182,
        label="已禁用项目自动切换视图",
        attempts=4,
        maximum_mean_difference=4.0,
    )
    await _click_until_screen(
        desktop,
        x=971,
        y=758,
        expected=RUYISDK_WORKBENCH_LAYOUT,
        expected_x=68,
        expected_y=164,
        label="RuyiSDK工作台保持打开",
        attempts=4,
    )


async def _create_cdt_project(desktop: Guest) -> None:
    """Create a CDT RISC-V project that can receive a Ruyi Venv."""

    ubuntu = UbuntuAdapter(desktop)
    await _show_workbench(ubuntu, label="创建CDT项目的RuyiSDK工作台")
    await _keep_ruyi_perspective_for_new_projects(desktop)
    await _click_until_screen(
        desktop,
        x=85,
        y=81,
        expected=RUYISDK_FILE_RESTART_MENU,
        expected_x=67,
        expected_y=95,
        label="Eclipse文件菜单已打开",
        attempts=4,
    )
    before_new_submenu = await desktop.screen.snapshot()
    await desktop.screen.move(445, 107)
    await desktop.screen.wait_for_change(before_new_submenu, timeout=30)
    await desktop.screen.click(550, 133)
    await desktop.screen.assert_screen(
        RUYISDK_CDT_PROJECT_WIZARD,
        x=369,
        y=32,
        timeout=30,
        label="CDT新建项目向导",
    )
    before_template_selection = await desktop.screen.snapshot()
    await desktop.screen.click(678, 175)
    await desktop.screen.wait_for_change(
        before_template_selection,
        timeout=30,
        minimum_changed_pixels=1_000,
    )
    before_project_type = await desktop.screen.snapshot()
    await desktop.screen.click(678, 669)
    await desktop.screen.wait_for_change(before_project_type, timeout=30)
    before_project_template = await desktop.screen.snapshot()
    await desktop.screen.click(550, 469)
    await desktop.screen.wait_for_change(
        before_project_template,
        timeout=30,
        minimum_changed_pixels=1_000,
    )
    before_toolchain_selection = await desktop.screen.snapshot()
    await desktop.screen.click(840, 421)
    await desktop.screen.wait_for_change(
        before_toolchain_selection,
        timeout=30,
        minimum_changed_pixels=1_000,
    )
    await _type_into(desktop, CDT_PROJECT_NAME, x=700, y=172)
    await desktop.screen.assert_screen(
        RUYISDK_CDT_PROJECT_SELECTED,
        x=383,
        y=154,
        timeout=30,
        label="CDTRISC-V项目模板已选择",
    )
    await _click_until_screen(
        desktop,
        x=852,
        y=767,
        expected=RUYISDK_CDT_SELECT_CONFIGURATIONS,
        expected_x=370,
        expected_y=82,
        label="CDT配置选择页",
    )
    await _click_until_screen(
        desktop,
        x=852,
        y=767,
        expected=RUYISDK_CDT_TOOLCHAIN,
        expected_x=370,
        expected_y=82,
        label="CDT工具链页",
    )
    await _click_until_screen(
        desktop,
        x=1205,
        y=767,
        expected=RUYISDK_IDE_SHELL,
        expected_x=66,
        expected_y=68,
        label="CDT项目创建向导已完成",
        timeout=60,
    )
    # Establish a known project-tree selection instead of depending on which
    # widget Eclipse happened to focus after completing the wizard.
    await _click_until_screen(
        desktop,
        x=160,
        y=264,
        expected=RUYISDK_CDT_PROJECT_TREE_RUYI_PERSPECTIVE,
        expected_x=110,
        expected_y=226,
        label="可绑定RuyiVenv的CDT项目已创建",
    )


async def _configure_generic_no_sysroot_venv(desktop: Guest) -> None:
    """Choose and verify the documented generic no-sysroot Venv settings."""

    # This table can retain an unrelated keyboard focus after the wizard
    # appears, so use the standard pointer wheel over the visible Profile
    # control.  The first entries include the documented generic profile.
    await desktop.screen.scroll(150, 304, 30)
    await desktop.screen.click(150, 306)
    # The documented GNU upstream option is visible in the generic profile's
    # toolchain table. Selecting it by row is robust when package-index
    # updates add rows elsewhere in the list.
    await _click_until_screen(
        desktop,
        x=750,
        y=296,
        expected=RUYISDK_VENV_GNU_UPSTREAM_SELECTED,
        expected_x=679,
        expected_y=None,
        label="RuyiVenv已选择GNU upstream工具链",
        attempts=5,
        maximum_mean_difference=2.0,
        move_after=(500, 700),
    )
    await _click_until_screen(
        desktop,
        x=1040,
        y=247,
        expected=RUYISDK_VENV_VERSION_SELECTED,
        expected_x=1100,
        expected_y=None,
        label="RuyiVenv工具链版本已选择",
        attempts=3,
        maximum_mean_difference=2.0,
        timeout=30,
        move_after=(500, 700),
    )
    await _click_until_screen(
        desktop,
        x=88,
        y=441,
        expected=RUYISDK_VENV_NO_SYSROOT,
        expected_x=76,
        expected_y=405,
        label="RuyiVenv不包含sysroot",
        attempts=6,
        maximum_mean_difference=2.0,
    )
    await _click_until_screen(
        desktop,
        x=976,
        y=710,
        expected=RUYISDK_VENV_LOCATION_SUMMARY_STABLE,
        expected_x=76,
        expected_y=180,
        label="RuyiVenv配置汇总",
        attempts=6,
        maximum_mean_difference=2.0,
    )


async def _read_ruyi_news_040(desktop: Guest) -> None:
    """Read the first published release note and require its real content."""

    await desktop.screen.click(350, 298)
    await desktop.screen.move(700, 780)
    await desktop.screen.assert_screen(
        RUYISDK_NEWS_DETAIL_040,
        x=111,
        y=482,
        maximum_mean_difference=4.0,
        timeout=30,
        label="RuyiNews0.40正文已打开",
    )
    # The detail pane overlaps the target row. Hide it before checking that
    # opening the article removed its unread marker while neighbours remain.
    await desktop.screen.click(1190, 740)
    await desktop.screen.assert_screen(
        RUYISDK_NEWS_READ,
        x=111,
        y=450,
        maximum_mean_difference=1.0,
        timeout=30,
        label="RuyiNews0.40已读",
    )


async def _search_ruyi_news(desktop: Guest, value: str) -> None:
    """Filter Ruyi News through its visible SWT search field."""

    await _show_workbench(UbuntuAdapter(desktop), label="RuyiNews搜索前工作台")
    await _type_into(desktop, value, x=300, y=222)
    await desktop.screen.move(700, 780)


async def _disable_egress(desktop: Guest) -> None:
    """Disconnect the declared egress NIC through QEMU's control plane."""

    await desktop.network.disconnect(RUYISDK_EGRESS)


async def _show_applied_venv(desktop: Guest) -> None:
    """Dismiss the apply result and return to the selected full-width Venv row."""

    await desktop.keyboard.press("ENTER")
    await _click_until_screen(
        desktop,
        x=1186,
        y=181,
        expected=RUYISDK_VENV_INFO_PROFILE,
        expected_x=600,
        expected_y=270,
        label="已应用RuyiVenv详情",
        maximum_mean_difference=2.0,
    )


async def _restore_project_explorer(desktop: Guest) -> None:
    """Close the maximized Venv view and select the CDT project tree."""

    for attempt in range(3):
        # SWT can occasionally drop an RFB pointer event after an alert closes.
        # Keep the state change and its visual assertion together so a retry
        # never proceeds from the still-open Venv tab.
        before_close = await desktop.screen.snapshot()
        await desktop.screen.click(200, 181)
        try:
            # Eclipse must finish processing the tab close before another
            # pointer event selects Project Explorer. Consecutive VNC clicks
            # can otherwise be delivered as one SWT gesture.
            await desktop.screen.wait_for_change(before_close, timeout=10)
        except GuestControlError:
            if attempt == 2:
                raise
            continue
        await desktop.screen.click(120, 181)
        try:
            await desktop.screen.assert_screen(
                RUYISDK_CDT_PROJECT_TREE_RUYI_PERSPECTIVE,
                x=110,
                y=226,
                timeout=10 if attempt < 2 else 30,
                maximum_mean_difference=10.0,
                label="RuyiSDK项目浏览器已恢复",
            )
        except GuestControlError:
            if attempt == 2:
                raise
        else:
            await desktop.screen.move(500, 700)
            return


async def _install_ruyi_through_wizard(desktop: Guest) -> None:
    """Install Ruyi through the IDE wizard, retrying a transient download error."""

    for attempt in range(2):
        await desktop.screen.click(580, 607)
        await desktop.screen.assert_screen(
            RUYI_INSTALLATION_WELCOME,
            x=271,
            y=31,
            timeout=30,
            label="Ruyi安装欢迎页",
        )
        await desktop.screen.click(580, 607)
        await desktop.screen.assert_screen(
            RUYI_INSTALLATION_CONFIGURATION,
            x=271,
            y=31,
            timeout=60,
            label="Ruyi安装配置页",
        )
        await desktop.screen.click(580, 607)
        try:
            await desktop.screen.assert_screen(
                RUYI_INSTALLATION_COMPLETE,
                x=271,
                y=573,
                timeout=300,
                label="Ruyi安装完成",
            )
        except GuestControlError:
            if attempt:
                raise
            await desktop.screen.assert_screen(
                RUYI_INSTALLATION_FAILED,
                x=510,
                y=210,
                timeout=1,
                label="Ruyi安装下载失败",
            )
            await desktop.screen.click(782, 363)
            await desktop.screen.click(696, 607)
            await desktop.screen.assert_screen(
                RUYISDK_INSTALLATION_REQUIRED,
                x=271,
                y=31,
                timeout=30,
                label="Ruyi安装重试前提",
            )
        else:
            return


@add_test(
    inputs={
        "ide": {
            "version": RUYISDK_VERSION,
            "url": RUYISDK_URL,
            "size": RUYISDK_ARCHIVE_SIZE,
            "sha256": RUYISDK_SHA256,
        },
        "plugin": {
            "version": PLUGIN_VERSION,
            "url": PLUGIN_URL,
            "sha256": PLUGIN_SHA256,
        },
    }
)
async def 测试RuyiSDK插件已安装(desktop: Guest = use(DESKTOP)):
    """Boot Ubuntu, install the plugin, and require Ruyi auto-detection."""

    await desktop.screen.assert_screen(
        UBUNTU_INSTALLER_LANGUAGE,
        x=160,
        y=76,
        timeout=180,
        label="Ubuntu安装器语言页",
    )
    await desktop.screen.click(615, 432)
    await desktop.screen.assert_screen(
        UBUNTU_INSTALLER_PREPARING,
        x=160,
        y=76,
        timeout=60,
        label="Ubuntu安装器准备页",
    )
    await desktop.screen.click(1102, 98)
    await desktop.screen.assert_screen(
        UBUNTU_DESKTOP_DOCK,
        x=0,
        y=40,
        timeout=60,
        label="Ubuntu桌面已就绪",
    )
    debian = UbuntuAdapter(desktop)
    # Long-running UI checks must not be interrupted by GNOME blanking the
    # display or locking the ephemeral Live session.
    await debian.terminal.run("gsettings set org.gnome.desktop.session idle-delay 0")
    await debian.terminal.run(
        "gsettings set org.gnome.desktop.screensaver lock-enabled false"
    )
    await debian.terminal.run("apt-get update", admin=True, timeout=300)
    await debian.terminal.run(
        "apt-get install --yes file make unzip xdotool", admin=True, timeout=300
    )
    home = (await debian.terminal.output('printf %s "$HOME"')).strip()
    uid = (await debian.terminal.output("id -u")).strip()
    gid = (await debian.terminal.output("id -g")).strip()
    data_root = "/mnt/catsnail-ruyisdk"
    await debian.terminal.run("test -b /dev/vda")
    await debian.terminal.run("mkfs.ext4 -F /dev/vda", admin=True, timeout=120)
    await debian.terminal.run(
        f"mkdir -p {data_root} && mount /dev/vda {data_root} && "
        f"mkdir -p {data_root}/cache {data_root}/opt {data_root}/bin "
        f"{data_root}/ruyi && chown -R {uid}:{gid} {data_root}",
        admin=True,
    )
    bindings = {
        f"{data_root}/cache": f"{home}/.cache",
        f"{data_root}/opt": f"{home}/.local/opt",
        f"{data_root}/bin": f"{home}/.local/bin",
        f"{data_root}/ruyi": f"{home}/.local/share/ruyi",
    }
    for source, destination in bindings.items():
        await debian.terminal.run(f"mkdir -p {shlex.quote(destination)}")
        await debian.terminal.run(
            f"mount --bind {shlex.quote(source)} {shlex.quote(destination)}",
            admin=True,
        )
    download_directory = f"{home}/Downloads"
    ide_home = f"{home}/.local/opt/ruyisdk-{RUYISDK_VERSION}"
    ide_executable = f"{ide_home}/ruyisdk"
    ide_archive = f"{download_directory}/ruyisdk-{RUYISDK_VERSION}.tar.gz"
    plugin_archive = (
        f"{download_directory}/ruyisdk-eclipse-plugins-{PLUGIN_VERSION}.zip"
    )
    plugin_repository = f"jar:file:{plugin_archive}!/"

    await desktop.screen.assert_screen(UBUNTU_DESKTOP_DOCK, x=0, y=40, timeout=120)

    # Entering a whole installation script through a virtual PS/2 keyboard can
    # overwhelm QEMU's input queue. Short commands also make the failing
    # install stage obvious in the recording and guest-command error.
    await debian.terminal.run(
        f"mkdir -p {shlex.quote(download_directory)} "
        f"{shlex.quote(str(Path(ide_home).parent))}"
    )
    await debian.terminal.run(
        _download_command(RUYISDK_URL, ide_archive),
        timeout=1_200,
    )
    await debian.terminal.run(
        f"stat --format=%s {shlex.quote(ide_archive)} | grep -qx {RUYISDK_ARCHIVE_SIZE}"
    )
    await debian.terminal.assert_output(
        f"sha256sum {shlex.quote(ide_archive)} | cut -d ' ' -f1",
        RUYISDK_SHA256,
    )
    await debian.terminal.run(f"rm -rf {shlex.quote(ide_home)}")
    await debian.terminal.run(f"mkdir -p {shlex.quote(ide_home)}")
    await debian.terminal.run(
        f"tar --extract --gzip --file={shlex.quote(ide_archive)} "
        f"--directory={shlex.quote(ide_home)} --strip-components=1",
        timeout=300,
    )
    await debian.terminal.run(f"test -x {shlex.quote(ide_executable)}")
    await debian.terminal.run(
        f"find {shlex.quote(ide_home)}/plugins -maxdepth 1 "
        "-name 'org.eclipse.ui.workbench_*.jar' -print -quit "
        "| xargs -r unzip -tq",
        timeout=120,
    )
    await debian.terminal.run(
        _download_command(PLUGIN_URL, plugin_archive),
        timeout=300,
    )
    await debian.terminal.assert_output(
        f"sha256sum {shlex.quote(plugin_archive)} | cut -d ' ' -f1",
        PLUGIN_SHA256,
    )
    await debian.terminal.run(
        f"{shlex.quote(ide_executable)} -nosplash "
        "-application org.eclipse.equinox.p2.director "
        f"-repository {shlex.quote(plugin_repository)} "
        "-installIU org.ruyisdk.feature.feature.group",
        timeout=600,
    )
    await debian.terminal.run(
        f"find {shlex.quote(ide_home)}/plugins -maxdepth 1 "
        "-name 'org.ruyisdk.core_*.jar' -print -quit | grep -q ."
    )
    # The IDE and p2 director write hundreds of bundles onto the reusable
    # guest disk. Validate the installed set and flush it before this test
    # becomes a checkpoint: a truncated JAR otherwise surfaces much later as
    # an unrelated SWT "Update Job" failure when a Venv wizard first loads.
    await debian.terminal.run(
        f"find {shlex.quote(ide_home)}/plugins -maxdepth 1 -type f "
        "-name '*.jar' -print0 | xargs -0 -r -n 1 unzip -tq",
        timeout=600,
    )
    await debian.terminal.run("sync", timeout=120)
    await debian.window.minimize()
    await desktop.screen.assert_screen(UBUNTU_DESKTOP_DOCK, x=0, y=40, timeout=60)
    workspace = f"{home}/ruyisdk-workspace"
    await debian.terminal.launch(
        # SWT's GTK text controls need a local XIM in the headless Ubuntu
        # session. Without it, synthetic VNC key events reach X11 but are
        # silently ignored by the Java/GTK input context.
        f"XMODIFIERS=@im=local GTK_IM_MODULE=xim QT_IM_MODULE=xim "
        f"{shlex.quote(ide_executable)} -data {shlex.quote(workspace)} "
        ">/tmp/ruyisdk-launch.log 2>&1 &",
        timeout=60,
    )
    await desktop.screen.assert_screen(
        RUYISDK_INSTALLATION_REQUIRED,
        x=271,
        y=31,
        timeout=120,
        label="RuyiSDK需要安装Ruyi",
    )


@add_test
async def 测试RuyiSDK自动检测与安装Ruyi(
    desktop: Guest = use(测试RuyiSDK插件已安装),
):
    """Install Ruyi through the IDE wizard and close its questionnaire."""

    await _install_ruyi_through_wizard(desktop)
    await desktop.screen.click(812, 608)
    await desktop.screen.assert_screen(
        RUYISDK_QUESTIONNAIRE,
        x=360,
        y=67,
        maximum_mean_difference=4.0,
        timeout=60,
        label="RuyiSDK调查问卷",
    )
    await desktop.screen.click(724, 564)
    await desktop.screen.assert_screen(
        RUYISDK_IDE_SHELL,
        x=66,
        y=68,
        timeout=120,
        label="Ruyi安装已结束",
    )


@add_test(internal=True)
async def 测试RuyiSDK工作台已就绪(
    desktop: Guest = use(测试RuyiSDK自动检测与安装Ruyi),
):
    """Close the Welcome page and verify the ordinary RuyiSDK workbench."""

    await desktop.screen.click(200, 180)
    await desktop.screen.assert_screen(
        RUYISDK_WORKBENCH_LAYOUT,
        x=68,
        y=164,
        timeout=60,
        label="RuyiSDK工作台已打开",
    )


@add_test
async def 测试RuyiSDK工作台菜单入口(
    desktop: Guest = use(测试RuyiSDK工作台已就绪),
):
    """Expose the plugin's management and view commands from the workbench."""

    await desktop.screen.click(528, 81)
    await desktop.screen.assert_screen(
        RUYISDK_MENU,
        x=490,
        y=97,
        timeout=30,
        label="RuyiSDK工作台菜单入口",
    )


@add_test
async def 测试RuyiSDK界面布局(
    desktop: Guest = use(测试RuyiSDK工作台已就绪),
):
    """Reset and restart Eclipse, then require the documented default layout."""

    await desktop.screen.click(634, 80)
    await desktop.screen.move(680, 212)
    await desktop.screen.assert_screen(
        RUYISDK_RESET_PERSPECTIVE_MENU,
        x=598,
        y=95,
        timeout=30,
        label="Reset Perspective菜单",
    )
    await _click_until_screen(
        desktop,
        x=850,
        y=292,
        expected=RUYISDK_RESET_PERSPECTIVE_CONFIRMATION,
        expected_x=291,
        expected_y=314,
        label="Reset Perspective确认对话框",
    )
    await _click_until_screen(
        desktop,
        x=780,
        y=438,
        expected=RUYISDK_WORKBENCH_LAYOUT,
        expected_x=68,
        expected_y=164,
        label="Reset Perspective已完成",
    )
    await desktop.screen.assert_screen(
        RUYISDK_WORKBENCH_LAYOUT,
        x=68,
        y=164,
        timeout=30,
        label="Reset Perspective工作台布局",
    )
    await desktop.screen.click(85, 80)
    await desktop.screen.assert_screen(
        RUYISDK_FILE_RESTART_MENU,
        x=67,
        y=95,
        timeout=30,
        label="Eclipse重启菜单",
    )
    await desktop.screen.click(125, 608)
    await desktop.screen.assert_screen(
        RUYISDK_QUESTIONNAIRE,
        x=None,
        y=68,
        maximum_mean_difference=8.0,
        timeout=120,
        label="重启后的RuyiSDK调查问卷",
    )
    await desktop.keyboard.press("TAB")
    await desktop.keyboard.press("ENTER")
    await desktop.screen.assert_screen(
        RUYISDK_DEFAULT_PACKAGE_LAYOUT,
        x=67,
        y=164,
        timeout=120,
        label="默认Ruyi Package Explorer布局",
    )
    await desktop.screen.assert_screen(
        RUYISDK_DEFAULT_VENV_LAYOUT,
        x=67,
        y=469,
        timeout=120,
        label="默认Ruyi Venv布局",
    )
    await desktop.screen.assert_screen(
        RUYISDK_DEFAULT_NEWS_LAYOUT,
        x=1052,
        y=164,
        timeout=120,
        label="默认Ruyi News布局",
    )
    await desktop.screen.assert_screen(
        RUYISDK_DEFAULT_WEBSITE_LAYOUT,
        x=1052,
        y=197,
        timeout=120,
        label="默认RuyiSDK Website布局",
    )


@add_test(internal=True)
async def 测试RuyiSDK包管理器已就绪(
    desktop: Guest = use(测试RuyiSDK界面布局),
):
    """Initialize the Package Explorer after the default perspective reset."""

    await _initialize_package_explorer(desktop)


@add_test
async def 测试包管理器按名称和ID排序(
    desktop: Guest = use(测试RuyiSDK包管理器已就绪),
):
    """Sort the device selector by name and ID, preserving visible ordering."""

    await _open_device_selector(desktop)
    await desktop.screen.click(403, 137)
    await desktop.screen.assert_screen(
        RUYISDK_DEVICE_SORT_NAME,
        x=355,
        y=120,
        timeout=30,
    )
    await desktop.screen.click(620, 137)
    await desktop.screen.assert_screen(
        RUYISDK_DEVICE_SORT_ID,
        x=355,
        y=120,
        timeout=30,
        label="按ID排序的开发板",
    )


@add_test
async def 测试包管理器按开发板筛选(
    desktop: Guest = use(测试RuyiSDK包管理器已就绪),
):
    """Select Milk-V Duo and assert that Package Explorer filters its packages."""

    await _select_milk_v_duo(desktop)


@add_test(
    expected_failure=(
        "#82: Package Operations cannot be closed during an active download; "
        "the background operation continues after the user requests the window close"
    )
)
async def 测试包管理器安装软件包(
    desktop: Guest = use(测试包管理器按开发板筛选),
):
    """Reproduce the documented unclosable Package Operations workflow defect."""

    await _select_latest_gnu_upstream(desktop, select_device=False)
    await _open_package_changes(desktop, label="GNU upstream安装缺陷复现")
    await desktop.screen.click(869, 522)
    # During an active operation the usual bottom-right position is a disabled
    # OK button, while the transient window has no active close affordance.
    # Asking its title bar to close therefore leaves the operation running.
    # This is the current manifestation of #82 in the released plug-in.
    operation_started = await desktop.screen.snapshot()
    await desktop.screen.click(963, 164)
    await desktop.screen.wait_for_change(
        operation_started, timeout=30, minimum_changed_pixels=100
    )
    await desktop.screen.assert_screen(
        RUYISDK_PACKAGE_OPERATION_COMPLETE,
        x=380,
        y=None,
        maximum_mean_difference=2.0,
        timeout=600,
        label="关闭请求后GNU upstream操作仍完成",
    )
    await desktop.screen.click(903, 522)
    await desktop.screen.assert_screen(
        RUYISDK_GNU_UPSTREAM_LATEST_INSTALLED,
        x=322,
        y=429,
        maximum_mean_difference=2.0,
        timeout=30,
        label="关闭请求后GNU upstream仍显示已安装",
    )
    xfail("Package Operations ignores the user's close request during a download")


@add_test(internal=True)
async def 测试包管理器安装软件包准备(
    desktop: Guest = use(测试RuyiSDK包管理器已就绪),
):
    """Install the toolchain once as a reusable prerequisite for package tests."""

    await _select_latest_gnu_upstream(desktop)
    await _apply_package_changes(
        desktop,
        RUYISDK_GNU_UPSTREAM_LATEST_INSTALLED,
        322,
        429,
        label="GNU upstream安装准备",
    )


@add_test
async def 测试RuyiSDK项目模板(
    desktop: Guest = use(测试包管理器安装软件包准备),
):
    """Create the documented Milk-V Duo RISC-V template project."""

    ubuntu = UbuntuAdapter(desktop)
    compiler = (
        await ubuntu.terminal.output(
            "find ~/.local/share/ruyi -type f "
            "-name riscv64-unknown-linux-gnu-gcc -print -quit"
        )
    ).strip()
    if not compiler:
        raise RuntimeError("Ruyi did not install a riscv64-unknown-linux-gnu compiler")

    # Keep the HTTP control server alive, but reveal the IDE for the wizard.
    await _show_workbench(ubuntu, label="RuyiSDK工作台")
    await desktop.keyboard.shortcut("CTRL", "N")
    await desktop.screen.assert_screen(RUYISDK_NEW_WIZARD, x=369, y=95, timeout=30)
    # The standard VNC/USB-tablet path reliably expands and selects this
    # wizard tree. Its native X11 alternative can leave the selection behind.
    before_expanding_ruyisdk_wizard = await desktop.screen.snapshot()
    await desktop.screen.click(390, 432)
    await desktop.screen.wait_for_change(
        before_expanding_ruyisdk_wizard,
        timeout=30,
        minimum_changed_pixels=50,
    )
    await desktop.screen.click(480, 445)
    await desktop.screen.assert_screen(
        RUYISDK_PROJECT_SELECTED,
        x=420,
        y=432,
        timeout=30,
        label="RuyiSDK项目向导已选择",
    )
    await desktop.keyboard.press("ENTER")
    await desktop.screen.assert_screen(RUYISDK_BOARD_MODEL, x=369, y=95, timeout=30)
    # The combo's keyboard activation is the stable normal-user path under
    # Xwayland: Space selects the default Milk-V Duo board.
    await desktop.screen.click(949, 234)
    await desktop.keyboard.press("SPACE")
    await desktop.screen.assert_screen(
        RUYISDK_PROJECT_SETTINGS,
        x=377,
        y=142,
        timeout=30,
        label="项目设置",
    )
    await _type_into(desktop, PROJECT_NAME, x=749, y=232)
    await _type_into(desktop, str(Path(compiler).parent), x=749, y=269)
    # Keep a real text control focused after both fields are entered. SWT
    # leaves a caret and blue outline in the active field, so moving to the
    # compiler flags field lets the two entered values be asserted precisely.
    await ubuntu.window.click(x=749, y=305)
    await desktop.screen.assert_screen(
        RUYISDK_PROJECT_SETTINGS_FILLED,
        x=377,
        y=142,
        maximum_mean_difference=2.0,
        timeout=30,
        label="项目设置已完整填写",
    )
    await _click_until_screen(
        desktop,
        x=911,
        y=570,
        expected=RUYISDK_IDE_SHELL,
        expected_x=66,
        expected_y=68,
        label="RuyiSDK项目已创建",
        attempts=3,
        timeout=60,
    )
    await desktop.screen.assert_screen(
        RUYISDK_IDE_SHELL,
        x=66,
        y=68,
        timeout=60,
    )
    await desktop.screen.click(200, 180)
    await desktop.screen.assert_screen(
        RUYISDK_PROJECT_TREE,
        x=70,
        y=196,
        timeout=60,
        label="RuyiSDK项目树",
    )

    await desktop.screen.assert_screen(
        RUYISDK_PROJECT_TREE,
        x=70,
        y=196,
        timeout=30,
        label="RuyiSDK项目模板已创建",
    )


@add_test
async def 测试RuyiSDK项目构建运行(
    desktop: Guest = use(测试RuyiSDK项目模板),
):
    """Build the template and verify its documented RISC-V ELF output."""

    ubuntu = UbuntuAdapter(desktop)
    compiler = (
        await ubuntu.terminal.output(
            "find ~/.local/share/ruyi -type f "
            "-name riscv64-unknown-linux-gnu-gcc -print -quit"
        )
    ).strip()
    if not compiler:
        raise RuntimeError("Ruyi did not install a riscv64-unknown-linux-gnu compiler")
    # The project wizard creates one board-specific Makefile per template.
    # Milk-V Duo is the board selected in 测试RuyiSDK项目模板.
    project = f"$HOME/ruyisdk-workspace/{PROJECT_NAME}/milkv-duo"
    toolchain = shlex.quote(str(Path(compiler).parent))
    cflags = shlex.quote("-Wall -O2 -march=rv64imafdc -mabi=lp64d")
    await ubuntu.terminal.run(
        f"cd {project} && make "
        f"CC={shlex.quote(compiler)} "
        f"OBJCOPY={toolchain}/riscv64-unknown-linux-gnu-objcopy "
        f"CFLAGS={cflags}",
        timeout=300,
    )
    await ubuntu.terminal.assert_run(
        f"cd {project} && file obj/hello.elf",
        "ELF",
        "RISC-V",
        timeout=300,
    )
    await _show_workbench(ubuntu, label="RISC-V项目已构建")
    await desktop.screen.assert_screen(
        RUYISDK_PROJECT_TREE,
        x=70,
        y=196,
        timeout=30,
        label="RISC-V项目树已构建",
    )


@add_test
async def 测试包管理器移除软件包(
    desktop: Guest = use(测试包管理器安装软件包准备),
):
    """Remove the GNU upstream package through the GUI."""

    await desktop.screen.assert_screen(
        RUYISDK_GNU_UPSTREAM_LATEST_INSTALLED,
        x=322,
        y=429,
        maximum_mean_difference=2.0,
        timeout=30,
        label="GNU upstream最新版已安装",
    )
    # The first child under GNU upstream is always the current latest version.
    # QEMU's USB tablet gives this checkbox reliable normal VNC pointer input;
    # unlike an X11 synthetic click it also exercises the user's input path.
    await desktop.screen.click(172, 441)
    # Clicking a checkbox also selects its tree row. Select an unrelated row
    # by its label so the unchecked target has the same visual state that a
    # user sees before applying the removal.
    await desktop.screen.click(250, 393)
    await desktop.screen.move(500, 700)
    await desktop.screen.assert_screen(
        RUYISDK_GNU_UPSTREAM_LATEST_UNSELECTED,
        x=164,
        y=429,
        maximum_mean_difference=2.0,
        timeout=30,
        label="GNU upstream最新版已取消选择",
    )
    await _apply_package_changes(
        desktop,
        RUYISDK_GNU_UPSTREAM_LATEST_REMOVED,
        130,
        405,
        label="GNU upstream移除",
    )


@add_test
async def 测试打开RuyiNews(
    desktop: Guest = use(测试RuyiSDK界面布局),
):
    """Open the Ruyi News view from the workbench view stack."""

    await _open_ruyi_news(desktop)


@add_test
async def 测试RuyiNews仅显示未读(
    desktop: Guest = use(测试打开RuyiNews),
):
    """Filter out a known read release note when only unread news is shown."""

    await _read_ruyi_news_040(desktop)
    before_filtering = await desktop.screen.snapshot()
    await desktop.screen.click(1142, 222)
    await desktop.screen.wait_for_change(
        before_filtering, timeout=30, minimum_changed_pixels=100
    )
    await desktop.screen.move(700, 780)
    await desktop.screen.assert_screen(
        RUYISDK_NEWS_UNREAD_RESULTS,
        x=111,
        y=450,
        maximum_mean_difference=1.0,
        timeout=30,
        label="已读新闻已从未读列表过滤",
    )


@add_test
async def 测试RuyiNews跟踪阅读状态(
    desktop: Guest = use(测试打开RuyiNews),
):
    """Persist the read state after closing and reopening Ruyi News."""

    await _read_ruyi_news_040(desktop)
    await desktop.screen.click(481, 180)
    await _open_ruyi_news(desktop)
    await desktop.screen.move(700, 780)
    await desktop.screen.assert_screen(
        RUYISDK_NEWS_READ,
        x=111,
        y=450,
        maximum_mean_difference=1.0,
        timeout=30,
        label="重新打开后RuyiNews仍为已读",
    )


@add_test
async def 测试RuyiNews搜索关键词(
    desktop: Guest = use(测试打开RuyiNews),
):
    """Search a stable release token in Ruyi News titles and IDs."""

    await _search_ruyi_news(desktop, "0.40")
    await desktop.screen.assert_screen(
        RUYISDK_NEWS_SEARCH_040,
        x=111,
        y=252,
        maximum_mean_difference=2.0,
        timeout=30,
        label="RuyiNews标题和ID搜索结果",
    )


@add_test
async def 测试RuyiNews离线缓存(
    desktop: Guest = use(测试打开RuyiNews),
):
    """Read a loaded release note after disabling the guest's egress NIC."""

    ubuntu = UbuntuAdapter(desktop)
    await _show_workbench(ubuntu, label="RuyiNews离线工作台")
    await _disable_egress(desktop)
    await _read_ruyi_news_040(desktop)


@add_test(
    expected_failure=(
        "white-theme Unread Only checkbox uses a white check mark and is "
        "not sufficiently visible"
    )
)
async def 测试RuyiNews白色主题未读标记(
    desktop: Guest = use(测试打开RuyiNews),
):
    """Exercise the unread filter in the documented light-theme defect state."""

    ubuntu = UbuntuAdapter(desktop)
    await ubuntu.terminal.run(
        "gsettings set org.gnome.desktop.interface color-scheme prefer-light"
    )
    await _show_workbench(ubuntu, label="白色主题RuyiNews工作台")
    await _open_ruyi_news(desktop)
    before_filtering = await desktop.screen.snapshot()
    await desktop.screen.click(1142, 222)
    await desktop.screen.wait_for_change(
        before_filtering, timeout=30, minimum_changed_pixels=100
    )
    await desktop.screen.move(700, 780)
    await desktop.screen.assert_screen(
        RUYISDK_NEWS_UNREAD_RESULTS,
        x=111,
        y=450,
        maximum_mean_difference=1.0,
        timeout=30,
        label="白色主题未读新闻列表",
    )
    xfail("Unread Only is not visibly checked in the light theme")


@add_test(internal=True)
async def 测试RuyiVenv配置已就绪(
    desktop: Guest = use(测试RuyiSDK界面布局),
):
    """Open Ruyi Venv after its package index has completed its first update."""

    await _open_venv_configuration(desktop)
    await desktop.screen.assert_screen(
        RUYISDK_VENV_CONFIGURATION,
        x=75,
        y=76,
        timeout=30,
        label="RuyiVenv配置检查点",
    )


@add_test
async def 测试新建RuyiVenv时Profile列表排序(
    desktop: Guest = use(测试RuyiVenv配置已就绪),
):
    """Sort virtual-environment profiles by name and required quirks."""

    await desktop.screen.click(160, 251)
    await desktop.screen.assert_screen(
        RUYISDK_VENV_PROFILE_NAME,
        x=76,
        y=235,
        timeout=30,
        label="按名称排序的Profile",
    )
    await desktop.screen.click(350, 251)
    await desktop.screen.assert_screen(
        RUYISDK_VENV_PROFILE_QUIRKS,
        x=76,
        y=235,
        timeout=30,
        label="按Quirks排序的Profile",
    )


@add_test
async def 测试RuyiVenv默认名称需要项目(
    desktop: Guest = use(测试RuyiVenv配置已就绪),
):
    """Require user input before accepting the default virtual-environment name."""

    # A bare default must not create a colliding virtual environment. The
    # wizard keeps Finish disabled until the user checks its generated name,
    # path, and required selections.
    await desktop.screen.assert_screen(
        RUYISDK_VENV_DEFAULT_FINISH_DISABLED,
        x=1152,
        y=692,
        maximum_mean_difference=2.0,
        timeout=30,
        label="RuyiVenv默认名称需要确认",
    )


@add_test(
    expected_failure=(
        "selecting the llvm-upstream no-sysroot toolchain still leaves the "
        "toolchain-provided sysroot option selected"
    )
)
async def 测试RuyiVenv无sysroot工具链过滤(
    desktop: Guest = use(测试RuyiVenv配置已就绪),
):
    """Reproduce the visible sysroot choice left by a no-sysroot toolchain."""

    # This regression concerns only the first configuration page. The current
    # package index lists llvm-upstream as the no-sysroot toolchain at this
    # stable row. It should select "Do not include sysroot" automatically,
    # but leaves the incompatible toolchain-provided option selected.
    await _click_until_screen(
        desktop,
        x=720,
        y=367,
        expected=RUYISDK_VENV_SYSROOT_SELECTABLE,
        expected_x=76,
        expected_y=405,
        label="选择无sysroot工具链",
        attempts=5,
        maximum_mean_difference=2.0,
    )
    xfail("llvm-upstream leaves the toolchain-provided sysroot option selected")


@add_test
async def 测试新建RuyiVenv响应时间(
    desktop: Guest = use(测试RuyiSDK界面布局),
):
    """Verify that the Venv configuration page opens in the accepted time."""

    await _show_venv_configuration(desktop)
    await desktop.screen.assert_screen(
        RUYISDK_VENV_CONFIGURATION,
        x=75,
        y=76,
        timeout=30,
        label="New Virtual environment配置页",
    )


@add_test(internal=True)
async def 测试RuyiVenv可用CDT项目(
    desktop: Guest = use(测试RuyiSDK界面布局),
):
    """Create a separate CDT RISC-V project for Venv acceptance tests."""

    await _create_cdt_project(desktop)


@add_test
async def 测试项目绑定RuyiVenv(
    desktop: Guest = use(测试RuyiVenv可用CDT项目),
):
    """Offer existing CDT projects instead of requesting a hand-written path."""

    await _show_venv_configuration(desktop)
    await _wait_for_venv_index(desktop)
    await _configure_generic_no_sysroot_venv(desktop)
    await desktop.screen.click(720, 585)
    await _click_until_screen(
        desktop,
        x=300,
        y=646,
        expected=RUYISDK_VENV_PROJECT_SELECTED,
        expected_x=164,
        expected_y=574,
        label="RuyiVenv可选择现有CDT项目",
    )


@add_test
async def 测试创建并应用RuyiVenv(
    desktop: Guest = use(测试RuyiVenv可用CDT项目),
):
    """Create the selected no-sysroot Venv and apply it to its CDT project."""

    await _show_venv_configuration(desktop)
    await _wait_for_venv_index(desktop)
    await _configure_generic_no_sysroot_venv(desktop)
    await desktop.screen.click(720, 585)
    await _click_until_screen(
        desktop,
        x=300,
        y=646,
        expected=RUYISDK_VENV_PROJECT_SELECTED,
        expected_x=164,
        expected_y=574,
        label="创建RuyiVenv已选择CDT项目",
        maximum_mean_difference=2.0,
    )
    await _click_until_screen(
        desktop,
        x=1205,
        y=710,
        expected=RUYISDK_VENV_CREATED_PROJECT,
        expected_x=108,
        expected_y=270,
        label="RuyiVenv已创建",
        timeout=60,
        maximum_mean_difference=2.0,
    )
    await _click_until_screen(
        desktop,
        x=160,
        y=280,
        expected=RUYISDK_VENV_INFO_PROJECT,
        expected_x=108,
        expected_y=270,
        label="RuyiVenv已创建并关联项目",
        maximum_mean_difference=2.0,
        move_after=(500, 700),
    )
    await desktop.screen.assert_screen(
        RUYISDK_VENV_INFO_PROFILE,
        x=600,
        y=270,
        maximum_mean_difference=2.0,
        timeout=60,
        label="RuyiVenv工具链信息已显示",
    )
    await desktop.screen.click(500, 280)
    await _click_until_screen(
        desktop,
        x=265,
        y=741,
        expected=RUYISDK_VENV_APPLIED,
        expected_x=418,
        expected_y=280,
        timeout=60,
        label="RuyiVenv已应用到CDT项目",
        maximum_mean_difference=2.0,
    )


@add_test
async def 测试RuyiVenv信息(
    desktop: Guest = use(测试创建并应用RuyiVenv),
):
    """Show the selected Venv's project, profile, toolchain, and QEMU state."""

    await _show_applied_venv(desktop)
    await desktop.screen.assert_screen(
        RUYISDK_VENV_INFO_PROFILE,
        x=600,
        y=270,
        maximum_mean_difference=2.0,
        timeout=30,
        label="RuyiVenv配置详情",
    )


@add_test(
    expected_failure=(
        "the active RUYI_VENV is not exposed by a direct, visible workbench "
        "status indicator"
    )
)
async def 测试RuyiVenv激活状态可见(
    desktop: Guest = use(测试创建并应用RuyiVenv),
):
    """Check the applied Venv state and document its missing visible indicator."""

    # The applied-Venv checkpoint already presents this table. Reopening the
    # view can move focus into an Eclipse Error Log tab, hiding the state this
    # scenario is meant to assess.
    await desktop.screen.assert_screen(
        RUYISDK_VENV_INFO_PROJECT,
        x=108,
        y=270,
        maximum_mean_difference=2.0,
        timeout=30,
        label="已应用RuyiVenv的项目行",
    )
    # The documented workaround is buried in project properties. A passing
    # implementation should expose this state directly in the Venv/workbench.
    xfail("active RuyiVenv has no direct visible status indicator")


@add_test
async def 测试项目右键RuyiSDK扩展(
    desktop: Guest = use(测试创建并应用RuyiVenv),
):
    """Expose New, Apply, and Delete Venv actions from a project context menu."""

    await _show_applied_venv(desktop)
    # Applying a Venv leaves its full-width view open. Close that user-facing
    # tab before choosing Project Explorer; merely selecting the tab while the
    # Venv view is maximized leaves the project's tree hidden.
    await _restore_project_explorer(desktop)
    for attempt in range(3):
        # Eclipse's Xwayland SWT Project Explorer ignores both RFB right-click
        # and Shift+F10 here. The standard Menu key is its reliable keyboard
        # equivalent once the selected project owns focus.
        await desktop.screen.click(160, 239)
        await desktop.keyboard.press("MENU")
        # The Project Explorer's bottom two entries are the plugin's
        # RuyiSDK submenu and Properties. End then Up avoids ambiguous menu
        # mnemonic matching (for example, "Show In" also contains R).
        await desktop.keyboard.press("END")
        await desktop.keyboard.press("UP")
        await desktop.keyboard.press("RIGHT")
        try:
            await desktop.screen.assert_screen(
                RUYISDK_PROJECT_CONTEXT,
                x=None,
                y=682,
                maximum_mean_difference=4.0,
                timeout=10 if attempt < 2 else 30,
                label="项目右键RuyiSDK扩展",
            )
        except GuestControlError:
            if attempt == 2:
                raise
            await desktop.keyboard.press("ESC")
            await asyncio.sleep(0.5)
        else:
            return


@add_test
async def 测试删除RuyiVenv后项目目录更新(
    desktop: Guest = use(测试创建并应用RuyiVenv),
):
    """Delete the Venv and require its project directory to disappear too."""

    await _show_applied_venv(desktop)
    await desktop.screen.click(500, 304)
    await _click_until_screen(
        desktop,
        x=365,
        y=741,
        expected=RUYISDK_VENV_DELETE_CONFIRMATION,
        expected_x=390,
        expected_y=264,
        label="删除RuyiVenv确认",
        attempts=4,
    )
    # The confirmation dialog focuses its green OK button. ENTER is the same
    # action a user performs and is not affected by the dialog's shifting
    # horizontal position.
    await desktop.keyboard.press("ENTER")
    await desktop.screen.assert_screen(
        RUYISDK_VENV_EMPTY,
        x=100,
        y=164,
        timeout=60,
        label="RuyiVenv已删除",
    )
    await desktop.screen.click(200, 180)
    await desktop.screen.click(77, 263)
    await desktop.screen.assert_screen(
        RUYISDK_PROJECT_VENV_DIRECTORY_REMOVED,
        x=70,
        y=255,
        timeout=60,
        label="删除后项目目录已更新",
    )
