# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What widgets share: connecting a signal without keeping the widget alive.

    connect_weak(obj, signal, self._method)   # the handler id
    connect_weak(obj, signal, self._method, data, …)   # data after the signal's arguments
    connect_weak_call(obj, signal, self.method, arg, …)  # self.method(arg, …), no signal args

A widget that can be dropped (a pushed page, a dialog, a row) never connects a child's or an
owned object's signal to its own bound method directly: the closure holds the widget, the
widget holds the child, and the cycle runs through C, so the widget is never freed.
connect_weak holds the method's object through a GObject weak reference (not weakref:
PyGObject may drop a widget's Python wrapper while the widget lives and make a new one) and
disconnects itself once the object is gone.
"""


def _unbound(method):
    """The function of a bound method, whether Python's or a GObject class's own (Gtk's)."""
    function = getattr(method, '__func__', None)
    if function is not None:
        return function
    name = method.__name__
    return lambda instance, *args: getattr(instance, name)(*args)


def connect_weak(obj, signal, method, *data):
    ref = method.__self__.weak_ref()
    function = _unbound(method)
    handler = None

    def call(emitter, *args):
        nonlocal handler
        instance = ref()
        if instance is None:
            if handler is not None and emitter.handler_is_connected(handler):
                emitter.disconnect(handler)
            return None
        return function(instance, emitter, *args, *data)

    handler = obj.connect(signal, call)
    return handler


def connect_weak_call(obj, signal, method, *args, **kwargs):
    """connect_weak for a method that takes none of the signal's arguments: the signal calls
    method(*args, **kwargs)."""
    ref = method.__self__.weak_ref()
    function = _unbound(method)
    handler = None

    def call(emitter, *_signal_args):
        instance = ref()
        if instance is None:
            if handler is not None and emitter.handler_is_connected(handler):
                emitter.disconnect(handler)
            return None
        return function(instance, *args, **kwargs)

    handler = obj.connect(signal, call)
    return handler
