"""Fullscreen, keyboard-accessible GTK3 Store Alpha."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .catalog import CatalogError, DeviceProfile, StoreApp, incompatibility_reasons
from .client import PROTECTED_PACKAGES, StoreClient
from .transactions import PackageTransaction, PK_FILTER_INSTALLED

APP_ID = "ai.typixdeck.store"
CSS = b"""
* { font-family: 'Noto Sans CJK SC', 'Noto Sans', sans-serif; }
window, .store-root { background: #071018; color: #eef8ff; }
.store-title { font-size: 25px; font-weight: 800; color: #f5fbff; }
.detail-title { font-size: 26px; font-weight: 700; color: #f5fbff; }
.muted { color: #8da6b8; font-size: 12px; }
.accent { color: #6eddfb; }
button { min-height: 38px; border: 1px solid #274354; border-radius: 11px;
         background: #102331; color: #d9f5ff; box-shadow: none; padding: 4px 11px; }
button:hover, button:focus { border-color: #43d5ff; background: #17384a; }
button:disabled { color: #647988; background: #0d1b25; }
button.suggested-action { background: #17637a; border-color: #43d5ff; }
entry { min-height: 34px; background: #0d1d29; color: #eef8ff; border: 1px solid #274354; border-radius: 10px; }
entry:focus { border-color: #43d5ff; }
list { background: #071018; color: #eef8ff; }
row { padding: 5px; border-radius: 12px; border: 1px solid transparent; }
row:selected { background: #123142; border-color: #43d5ff; }
row:focus { border: 2px solid #43d5ff; }
progressbar trough { background: #102331; border: none; }
progressbar progress { background: #43d5ff; border: none; }
scrollbar { background: transparent; }
scrollbar slider { min-width: 7px; min-height: 40px; background: #35576a; border-radius: 8px; }
"""


def label(text="", style=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    if style:
        widget.get_style_context().add_class(style)
    if wrap:
        widget.set_line_wrap(True)
        widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        widget.set_max_width_chars(45)
    return widget


class StoreApplication(Gtk.Application):
    def __init__(self, client: StoreClient | None = None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.client = client or StoreClient()
        self.window = None
        self.apps = []
        self.installed = {}
        self.updates = {}
        self.selected = None
        self.profile = DeviceProfile()
        self.desktop = None
        self.source_error = "正在检查系统安装源…"
        self.busy = False
        self.transaction = None
        self.cancel_event = threading.Event()
        self.rows = {}
        self._mapped_once = False

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title("TypixDeck 应用商店")
        self.window.set_default_size(800, 600)
        self.window.connect("key-press-event", self.on_key)
        self.window.connect("delete-event", self.on_close)
        self.window.connect("map-event", self.on_first_map)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(16)
        root.get_style_context().add_class("store-root")
        self.window.add(root)
        header = Gtk.Box(spacing=12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_box.pack_start(label("应用商店", "store-title"), False, False, 0)
        self.source_subtitle = label("ALPHA  ·  完整应用软件包", "accent")
        title_box.pack_start(self.source_subtitle, False, False, 0)
        header.pack_start(title_box, True, True, 0)
        self.refresh_button = Gtk.Button(label="刷新")
        self.refresh_button.connect("clicked", lambda *_: self.reload())
        header.pack_start(self.refresh_button, False, False, 0)
        close_button = Gtk.Button(label="返回桌面")
        close_button.connect("clicked", lambda *_: self.window.close())
        header.pack_start(close_button, False, False, 0)
        root.pack_start(header, False, False, 0)

        search_box = Gtk.Box(spacing=10)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("搜索应用")
        self.search.connect("search-changed", lambda *_: self.filter_rows())
        search_box.pack_start(self.search, True, True, 0)
        self.installed_filter = Gtk.CheckButton(label="已安装")
        self.installed_filter.connect("toggled", lambda *_: self.filter_rows())
        search_box.pack_start(self.installed_filter, False, False, 0)
        root.pack_start(search_box, False, False, 0)

        body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_position(248)
        body.set_vexpand(True)
        root.pack_start(body, True, True, 0)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_min_content_width(210)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.connect("row-selected", self.on_selected)
        list_scroll.add(self.list_box)
        body.pack1(list_scroll, False, False)
        # Scroll only the description/details; the entire action panel stays
        # visible at 800 x 600, including when technical details are expanded.
        detail_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail_column.set_border_width(10)
        body.pack2(detail_column, True, False)
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_min_content_height(80)
        detail_scroll.set_vexpand(True)
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail_scroll.add(detail)
        detail_column.pack_start(detail_scroll, True, True, 0)
        self.detail_title = label("正在载入可信目录…", "detail-title", True)
        self.detail_icon = Gtk.Image.new_from_icon_name("system-software-install", Gtk.IconSize.DIALOG)
        title_row = Gtk.Box(spacing=12)
        title_row.pack_start(self.detail_icon, False, False, 0)
        title_row.pack_start(self.detail_title, True, True, 0)
        detail.pack_start(title_row, False, False, 0)
        self.detail_summary = label(style="accent", wrap=True)
        self.detail_description = label(wrap=True)
        self.detail_version = label(style="muted", wrap=True)
        for widget in (self.detail_summary, self.detail_description, self.detail_version):
            detail.pack_start(widget, False, False, 0)
        self.package_expander = Gtk.Expander(label="软件包详情")
        self.package_expander.set_can_focus(True)
        self.package_expander.set_expanded(False)
        self.detail_meta = label(style="muted", wrap=True)
        self.detail_meta.set_selectable(True)
        self.detail_meta.set_margin_top(8)
        self.package_expander.add(self.detail_meta)
        detail.pack_start(self.package_expander, False, False, 0)

        action_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.detail_status = label(wrap=True)
        self.detail_status.set_lines(2)
        self.detail_status.set_ellipsize(Pango.EllipsizeMode.END)
        action_panel.pack_start(self.detail_status, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.install_button = Gtk.Button(label="安装")
        self.install_button.get_style_context().add_class("suggested-action")
        self.install_button.connect("clicked", lambda *_: self.confirm_operation("install"))
        self.remove_button = Gtk.Button(label="移除应用")
        self.remove_button.connect("clicked", lambda *_: self.confirm_operation("remove"))
        actions.pack_start(self.install_button, True, True, 0)
        actions.pack_start(self.remove_button, True, True, 0)
        action_panel.pack_start(actions, False, False, 0)
        self.shortcut_button = Gtk.Button(label="添加到桌面")
        self.shortcut_button.connect("clicked", self.on_shortcut)
        action_panel.pack_start(self.shortcut_button, False, False, 0)
        for button in (self.install_button, self.remove_button, self.shortcut_button):
            button.set_size_request(-1, 48)
        detail_column.pack_start(action_panel, False, False, 0)
        self.progress_bar = Gtk.ProgressBar()
        root.pack_start(self.progress_bar, False, False, 0)
        bottom = Gtk.Box(spacing=10)
        self.status = label("正在验证目录签名…", "muted", True)
        self.status.set_lines(2)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.connect("notify::label", lambda widget, _prop: widget.set_tooltip_text(widget.get_text()))
        bottom.pack_start(self.status, True, True, 0)
        self.cancel_button = Gtk.Button(label="取消")
        self.cancel_button.connect("clicked", self.on_cancel)
        bottom.pack_start(self.cancel_button, False, False, 0)
        root.pack_start(bottom, False, False, 0)
        root.pack_start(label("Tab 切换 · 方向键选择 · Enter 操作 · Esc 返回 · Ctrl+F 搜索", "muted"), False, False, 0)
        self.window.fullscreen()
        self.window.show_all()
        self.reload()

    def worker(self, work, done):
        def run():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, str(exc)
            GLib.idle_add(done, result, error)
        threading.Thread(target=run, daemon=True, name="typix-store-worker").start()

    def set_busy(self, busy, cancel=False):
        self.busy = busy
        self.refresh_button.set_sensitive(not busy)
        self.cancel_button.set_sensitive(busy and cancel)
        self.render_details()

    def reload(self, message=None):
        if self.busy:
            return
        self.cancel_event.clear()
        self.set_busy(True, cancel=True)
        self.status.set_text("正在刷新应用源、验证签名与检查本机兼容性…")
        def work():
            apps = self.client.load_catalog(self.cancel_event.is_set)
            source_error = self.client.installation_source_error()
            profile = DeviceProfile.from_environment()
            installed = {app.id: self.client.installed_version(app) for app in apps}
            updates = {app.id: self.client.has_update(installed[app.id], app.current_version) for app in apps}
            try:
                desktop = self.client.desktop_directory()
            except CatalogError:
                desktop = None
            return apps, profile, installed, updates, desktop, source_error
        def done(result, error):
            previous = self.selected.id if self.selected else None
            self.selected = None
            self.apps = []
            for row in self.list_box.get_children():
                self.list_box.remove(row)
            self.rows.clear()
            if error:
                self.detail_title.set_text("无法加载可信目录")
                self.status.set_text(error + "。更新离线仓库后点击刷新。")
            else:
                self.apps, self.profile, self.installed, self.updates, self.desktop, self.source_error = result
                self.source_subtitle.set_text("ALPHA  ·  " + self.client.source_title)
                for app in self.apps:
                    row = Gtk.ListBoxRow()
                    row.app = app
                    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
                    box.set_border_width(8)
                    name = label(app.name())
                    name.set_ellipsize(Pango.EllipsizeMode.END)
                    summary = label(app.summary(), "muted", True)
                    state = ("新版待系统部署" if self.source_error else "可更新") if self.updates[app.id] else "已安装" if self.installed[app.id] else "可预览" if self.source_error else "未安装"
                    box.pack_start(name, False, False, 0)
                    box.pack_start(summary, False, False, 0)
                    box.pack_start(label(state, "accent"), False, False, 0)
                    row_content = Gtk.Box(spacing=4)
                    icon = Gtk.Image.new_from_icon_name(self.icon_name(app), Gtk.IconSize.DND)
                    icon.set_pixel_size(32)
                    row_content.pack_start(icon, False, False, 3)
                    row_content.pack_start(box, True, True, 0)
                    row.add(row_content)
                    self.list_box.add(row)
                    self.rows[app.id] = row
                self.list_box.show_all()
                self.status.set_text(message or self.source_error or self.client.source_notice)
            self.set_busy(False)
            self.filter_rows(previous)
            return False
        self.worker(work, done)

    def filter_rows(self, preferred=None):
        query = self.search.get_text().strip().casefold()
        visible = []
        for app in self.apps:
            show = query in (app.name() + " " + app.summary() + " " + app.package).casefold()
            show = show and (not self.installed_filter.get_active() or bool(self.installed.get(app.id)))
            self.rows[app.id].set_visible(show)
            if show:
                visible.append(self.rows[app.id])
        selected_id = preferred or (self.selected.id if self.selected else None)
        row = self.rows.get(selected_id)
        if row not in visible:
            row = visible[0] if visible else None
        self.list_box.select_row(row)
        if row is None:
            self.selected = None
            self.detail_title.set_text("没有匹配的应用" if self.apps else "离线目录不可用或尚无应用")
            self.render_details()

    @staticmethod
    def icon_name(app):
        fallback = {"typix-reader": "accessories-text-editor", "typix-gamer": "applications-games", "typix-myai": "audio-input-microphone"}
        return app.raw.get("icon") or fallback.get(app.package, "application-x-executable")

    def on_selected(self, _box, row):
        if self.selected is None or row is None or self.selected.id != row.app.id:
            self.package_expander.set_expanded(False)
        self.selected = row.app if row else None
        self.render_details()

    def render_details(self):
        if not hasattr(self, "install_button"):
            return
        app = self.selected
        for button in (self.install_button, self.remove_button, self.shortcut_button):
            button.set_sensitive(False)
        self.package_expander.set_visible(app is not None)
        if not app:
            for widget in (self.detail_summary, self.detail_description, self.detail_version, self.detail_meta, self.detail_status):
                widget.set_text("")
            return
        record = app.current_record()
        installed = self.installed.get(app.id)
        update = self.updates.get(app.id, False)
        self.detail_title.set_text(app.name())
        self.detail_icon.set_from_icon_name(self.icon_name(app), Gtk.IconSize.DIALOG)
        self.detail_summary.set_text(app.summary())
        self.detail_description.set_text(app.description())
        artifact = record["artifact"]
        depends = artifact["depends"] if isinstance(artifact["depends"], str) else ", ".join(artifact["depends"])
        self.detail_version.set_text(f"版本 {app.current_version} · {artifact['sizeBytes'] / 1024:.0f} KB")
        reasons = incompatibility_reasons(record, self.profile)
        self.detail_meta.set_text(f"{app.package}\n依赖：{depends}\n适用系统：{', '.join(record['compatibility']['os'])}\n本机：{self.profile.os} · {self.profile.arch}\n内存 {self.profile.memory_mb} MB · 可用空间 {self.profile.free_disk_mb} MB" + ("\n兼容性：" + "；".join(reasons) if reasons else "\n兼容性：通过"))
        self.detail_status.set_text(self.source_error or ("本机暂不兼容，展开软件包详情查看原因" if reasons else ("本机兼容 · " + (f"已安装 {installed}" + (" · 可更新" if update else "") if installed else "可安装"))))
        self.install_button.set_label("更新" if update else "已安装" if installed else "安装")
        self.install_button.set_sensitive(not self.busy and not self.source_error and not reasons and (not installed or update))
        self.remove_button.set_sensitive(not self.busy and not self.source_error and bool(installed) and app.package not in PROTECTED_PACKAGES)
        shortcut = self.desktop and self.client.shortcut_exists(app, self.desktop)
        self.shortcut_button.set_label("从桌面移除快捷方式" if shortcut else "添加到桌面")
        self.shortcut_button.set_sensitive(not self.busy and bool(installed or shortcut) and bool(app.desktop_file) and self.desktop is not None)

    def confirm_operation(self, action):
        if self.busy or not self.selected:
            return
        if self.source_error:
            self.status.set_text(self.source_error)
            return
        app = self.selected
        verb = "移除" if action == "remove" else "更新" if self.updates.get(app.id) else "安装"
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=f"{verb} {app.name()}？")
        dialog.format_secondary_text(f"{app.package} · {app.current_version}\n" + ("保留用户文档；如果其他应用依赖它，系统将拒绝移除。" if action == "remove" else ("将从配置的 GitHub 仓库下载完整 deb，并分别请求系统暂存与安装授权。缺失依赖可能需要网络。" if self.client.remote else "已验证离线签名。系统将检查依赖并请求授权；缺失依赖可能需要网络。")) + "\n提交期间请等待系统完成。")
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, verb, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self.begin_operation(app, action)

    def begin_operation(self, app, action):
        self.cancel_event.clear()
        self.set_busy(True, cancel=True)
        self.status.set_text("正在重新验证签名、软件包和系统状态…")
        def work():
            return self.client.prepare_remove(app) if action == "remove" else self.client.prepare_install(app, self.cancel_event.is_set, progress=lambda done, total: GLib.idle_add(self.download_progress, done, total))
        def done(result, error):
            if error or self.cancel_event.is_set():
                self.operation_finished(False, error or "操作已取消，尚未修改软件包")
                return False
            self.cancel_button.set_sensitive(False)
            self.status.set_text("等待系统授权…")
            if action == "remove":
                self.transaction = PackageTransaction(self.transaction_progress, lambda success, detail, packages: self.resolved_remove(app, success, detail, packages))
                self.transaction.start("Resolve", "(tas)", (PK_FILTER_INSTALLED, [app.package]))
            else:
                self.transaction = PackageTransaction(self.transaction_progress, lambda success, detail, _packages: self.operation_finished(success, detail))
                self.transaction.start("InstallFiles", "(tas)", (0, [str(result)]))
            return False
        self.worker(work, done)

    def resolved_remove(self, app, success, error, packages):
        if not success or self.cancel_event.is_set():
            self.operation_finished(False, error or "操作已取消")
            return
        matches = [package for package in packages if package.split(";")[0] == app.package]
        if len(matches) != 1:
            self.operation_finished(False, "无法确认唯一已安装软件包，请刷新后重试")
            return
        self.transaction = PackageTransaction(self.transaction_progress, lambda ok, detail, _packages: self.operation_finished(ok, detail))
        # Never remove dependent packages or automatically remove dependencies.
        self.transaction.start("RemovePackages", "(tasbb)", (0, matches, False, False))

    def download_progress(self, done, total):
        if done < 0:
            self.status.set_text("完整包下载校验完成；等待系统授权，独立验证并暂存软件包…")
            self.progress_bar.set_fraction(1)
        elif total:
            self.status.set_text(f"下载完整 deb：{done / 1048576:.1f} / {total / 1048576:.1f} MB · 可取消和续传")
            self.progress_bar.set_fraction(done / total)
        return False

    def transaction_progress(self, message, percent, allow_cancel):
        self.status.set_text(message)
        self.cancel_button.set_sensitive(allow_cancel)
        if percent is None:
            self.progress_bar.pulse()
        else:
            self.progress_bar.set_fraction(percent / 100)

    def operation_finished(self, success, detail):
        self.transaction = None
        self.set_busy(False)
        self.progress_bar.set_fraction(0)
        message = "系统事务完成；桌面快捷方式可单独管理。" if success else "操作未完成：" + (detail or "系统拒绝或取消了事务") + "。可刷新状态后重试；不会自动重放操作。"
        self.reload(message)

    def on_cancel(self, *_args):
        if not self.busy:
            return
        if self.transaction:
            if self.transaction.cancel():
                self.cancel_event.set()
                self.cancel_button.set_sensitive(False)
            else:
                self.status.set_text("系统正在提交或等待授权，当前不能取消；请等待完成。")
        else:
            self.cancel_event.set()
            self.status.set_text("正在取消准备操作…")
            self.cancel_button.set_sensitive(False)

    def on_shortcut(self, *_args):
        if self.busy or not self.selected or self.desktop is None:
            return
        app, desktop = self.selected, self.desktop
        enabled = not self.client.shortcut_exists(app, desktop)
        self.set_busy(True)
        def done(_result, error):
            self.set_busy(False)
            self.status.set_text(error or ("已添加桌面快捷方式，Launcher 会自动刷新。" if enabled else "已移除桌面快捷方式，应用仍然保留。"))
            return False
        self.worker(lambda: self.client.set_shortcut(app, desktop, enabled), done)

    def on_first_map(self, window, _event):
        if not self._mapped_once:
            self._mapped_once = True
            GLib.idle_add(window.fullscreen)
        return False

    def on_close(self, *_args):
        if self.busy:
            self.status.set_text("当前操作尚未结束；可用时点击取消，或等待系统完成后返回桌面。")
            return True
        return False

    def on_key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            if self.busy:
                self.on_cancel()
            else:
                self.window.close()
            return True
        if event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.search.grab_focus()
            return True
        if event.keyval == Gdk.KEY_F5:
            self.reload()
            return True
        return False


def main() -> int:
    GLib.set_prgname(APP_ID)
    return StoreApplication().run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
