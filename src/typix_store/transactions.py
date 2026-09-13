"""Asynchronous PackageKit D-Bus transaction, authorization handled by Polkit.

API: https://www.freedesktop.org/software/PackageKit/gtk-doc/Transaction.html
Only daemon-approved Cancel is used. No kill, dpkg subprocess, or root GUI.
"""
from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

SERVICE = "org.freedesktop.PackageKit"
INTERFACE = SERVICE + ".Transaction"
PK_EXIT_SUCCESS = 1
PK_FILTER_INSTALLED = 1 << 2


class PackageTransaction:
    def __init__(self, progress, finished):
        self.progress = progress
        self.finished_callback = finished
        self.connection = None
        self.closed_handler = None
        self.path = None
        self.subscriptions = []
        self.allow_cancel = False
        self.finished = False
        self.error = ""
        self.packages = []

    def start(self, method, signature, args):
        self.method, self.signature, self.args = method, signature, args
        Gio.bus_get(Gio.BusType.SYSTEM, None, self._bus_ready)

    def _bus_ready(self, _source, result, *_data):
        try:
            self.connection = Gio.bus_get_finish(result)
            self.closed_handler = self.connection.connect("closed", self._connection_closed)
            self.connection.call(SERVICE, "/org/freedesktop/PackageKit", SERVICE, "CreateTransaction", None,
                                 GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, 15000, None, self._created)
        except GLib.Error as exc:
            self._finish(False, f"PackageKit 不可用：{exc.message}")

    def _created(self, connection, result, *_data):
        try:
            self.path = connection.call_finish(result).unpack()[0]
            self.subscriptions.append(connection.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", "/org/freedesktop/DBus", SERVICE, Gio.DBusSignalFlags.NONE, self._owner_changed))
            self.subscriptions.append(connection.signal_subscribe(SERVICE, INTERFACE, None, self.path, None, Gio.DBusSignalFlags.NONE, self._signal))
            self.subscriptions.append(connection.signal_subscribe(SERVICE, "org.freedesktop.DBus.Properties", "PropertiesChanged", self.path, None, Gio.DBusSignalFlags.NONE, self._properties))
            self._call("SetHints", "(as)", (["interactive=true", "background=false", "cache-age=2147483647"],), self._hints_done)
        except GLib.Error as exc:
            self._finish(False, f"无法创建授权事务：{exc.message}")

    def _connection_closed(self, *_args):
        self._finish(False, "系统总线连接已断开，请刷新核对安装状态；不要自动重试未确认的事务")

    def _owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters, *_data):
        name, _old_owner, new_owner = parameters.unpack()
        if name == SERVICE and not new_owner:
            self._finish(False, "PackageKit 服务已断开，请刷新核对安装状态后再操作")

    def _call(self, method, signature, args, callback):
        self.connection.call(SERVICE, self.path, INTERFACE, method, GLib.Variant(signature, args), None,
                             Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None, callback)

    def _hints_done(self, connection, result, *_data):
        try:
            connection.call_finish(result)
            self._call(self.method, self.signature, self.args, self._started)
        except GLib.Error as exc:
            self._finish(False, exc.message)

    def _started(self, connection, result, *_data):
        try:
            connection.call_finish(result)
        except GLib.Error as exc:
            # A method reply can time out while daemon work continues. Keep the
            # signal subscription and block overlapping mutations until Finished.
            if "Timeout" in exc.message or "timed out" in exc.message.lower():
                self.progress("系统事务仍在等待响应；请等待完成，勿关闭电源", None, False)
                return
            self._finish(False, "系统授权被拒绝或事务失败：" + exc.message)

    def _signal(self, _connection, _sender, _path, _interface, signal, parameters, *_data):
        if self.finished:
            return
        values = parameters.unpack()
        if signal == "ErrorCode":
            self.error = str(values[1])
            self.progress("系统事务：" + self.error, None, self.allow_cancel)
        elif signal == "Package":
            self.packages.append(values[1])
        elif signal == "Finished":
            self._finish(values[0] == PK_EXIT_SUCCESS and not self.error, self.error)
        elif signal == "Destroy":
            self._finish(False, "事务连接已结束，请刷新并检查应用安装状态")

    def _properties(self, _connection, _sender, _path, _interface, _signal, parameters, *_data):
        interface, changed, _invalidated = parameters.unpack()
        if interface != INTERFACE or self.finished:
            return
        self.allow_cancel = bool(changed.get("AllowCancel", self.allow_cancel))
        percent = changed.get("Percentage")
        percent = percent if isinstance(percent, int) and 0 <= percent <= 100 else None
        message = "系统正在处理，可安全取消" if self.allow_cancel else "等待系统授权或正在提交，请等待完成"
        self.progress(message, percent, self.allow_cancel)

    def cancel(self):
        if not self.allow_cancel or self.finished or self.path is None:
            return False
        self.allow_cancel = False
        self._call("Cancel", "()", (), self._cancel_done)
        return True

    def _cancel_done(self, connection, result, *_data):
        try:
            connection.call_finish(result)
            self.progress("已请求系统安全取消，等待事务结束…", None, False)
        except GLib.Error as exc:
            self.progress("当前阶段不可取消，请等待系统完成：" + exc.message, None, False)

    def _finish(self, success, error):
        if self.finished:
            return
        self.finished = True
        self.allow_cancel = False
        if self.connection:
            if self.closed_handler is not None:
                self.connection.disconnect(self.closed_handler)
                self.closed_handler = None
            for subscription in self.subscriptions:
                self.connection.signal_unsubscribe(subscription)
        self.finished_callback(success, error, self.packages)
