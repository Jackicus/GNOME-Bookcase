# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""A fake Gio layer for an MTP reader: FakeFile answers the Gio.File calls devices.Storage
makes, over a real temporary folder, but as gvfs's MTP backend shows a device: an mtp://
URI, no local path, no moves (only renames in a folder), and deleting a folder takes what is
in it (as some MTP devices do, which devices.py must never rely on: it records
`deleted_full_folders`). FakeMount stands in for the Gio.Mount.

    root = FakeFile(folder, 'mtp://Fake_Kindle_0001/')   # the mount's root
    storage = devices.Storage(root, new_for_path=fake_new_for_path)
"""

import os
import shutil
from urllib.parse import quote

from gi.repository import Gio, GLib

deleted_full_folders = []


def _error(code, message):
    return GLib.Error.new_literal(Gio.io_error_quark(), message, code)


class _Info:
    def __init__(self, path):
        self.path = path
        self.stat = os.stat(path)

    def get_name(self):
        return os.path.basename(self.path)

    def get_file_type(self):
        return Gio.FileType.DIRECTORY if os.path.isdir(self.path) else Gio.FileType.REGULAR

    def get_size(self):
        return self.stat.st_size

    def get_is_hidden(self):
        return False

    def get_attribute_uint64(self, name):
        if name == 'time::modified':
            return int(self.stat.st_mtime)
        if name == 'filesystem::free':
            return 3 * 1000 ** 3
        if name == 'filesystem::size':
            return 8 * 1000 ** 3
        raise KeyError(name)

    def get_attribute_uint32(self, name):
        return int(self.stat.st_mtime_ns // 1000 % 1_000_000)


class _Enumerator:
    def __init__(self, path):
        self.infos = [_Info(os.path.join(path, name)) for name in sorted(os.listdir(path))]

    def next_file(self, _cancellable):
        return self.infos.pop(0) if self.infos else None

    def close(self, _cancellable):
        return True


class _Bytes:
    def __init__(self, data):
        self.data = data

    def get_data(self):
        return self.data


class _Stream:
    def __init__(self, path):
        self.file = open(path, 'rb')

    def read_bytes(self, count, _cancellable):
        return _Bytes(self.file.read(count))

    def close(self, _cancellable):
        self.file.close()


class FakeFile:
    """A file on the fake device (uri set) or a local file (uri None)."""

    def __init__(self, path, uri=None):
        self.path = os.path.abspath(path)
        self.base_uri = uri  # the mount root's URI, for a file on the device
        self.base_path = self.path if uri else None

    def _child(self, path):
        child = FakeFile(path)
        child.base_uri, child.base_path = self.base_uri, self.base_path
        return child

    # -- names -------------------------------------------------------------------------------

    def get_uri_scheme(self):
        return 'mtp' if self.base_uri else 'file'

    def get_path(self):
        return None if self.base_uri else self.path

    def get_uri(self):
        if not self.base_uri:
            return 'file://' + quote(self.path)
        rel = os.path.relpath(self.path, self.base_path)
        if rel == '.':
            return self.base_uri
        return self.base_uri.rstrip('/') + '/' + quote(rel.replace(os.sep, '/'))

    def get_basename(self):
        return os.path.basename(self.path)

    def get_parent(self):
        return self._child(os.path.dirname(self.path))

    def get_child(self, name):
        return self._child(os.path.join(self.path, name))

    def resolve_relative_path(self, rel):
        return self._child(os.path.normpath(os.path.join(self.path, rel)))

    def equal(self, other):
        return isinstance(other, FakeFile) and other.path == self.path

    def get_relative_path(self, other):
        rel = os.path.relpath(other.path, self.path)
        return None if rel.startswith('..') else rel.replace(os.sep, '/')

    # -- reading -----------------------------------------------------------------------------

    def query_file_type(self, _flags, _cancellable):
        if os.path.isdir(self.path):
            return Gio.FileType.DIRECTORY
        if os.path.isfile(self.path):
            return Gio.FileType.REGULAR
        return Gio.FileType.UNKNOWN

    def enumerate_children(self, _attributes, _flags, _cancellable):
        if not os.path.isdir(self.path):
            raise _error(Gio.IOErrorEnum.NOT_FOUND, 'No such folder')
        return _Enumerator(self.path)

    def read(self, _cancellable):
        if not os.path.isfile(self.path):
            raise _error(Gio.IOErrorEnum.NOT_FOUND, 'No such file')
        return _Stream(self.path)

    def query_filesystem_info(self, _attributes, _cancellable):
        return _Info(self.path)

    # -- writing -----------------------------------------------------------------------------

    def copy(self, dest, _flags, cancellable, progress, _data):
        if cancellable is not None and cancellable.is_cancelled():
            raise _error(Gio.IOErrorEnum.CANCELLED, 'Operation was cancelled')
        size = os.path.getsize(self.path)
        with open(self.path, 'rb') as source, open(dest.path, 'wb') as target:
            done = 0
            while chunk := source.read(4096):
                target.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, size, None)
        return True

    def move(self, dest, _flags, _cancellable, _progress, _data):
        if self.base_uri:
            raise _error(Gio.IOErrorEnum.NOT_SUPPORTED, 'Operation not supported')
        os.replace(self.path, dest.path)
        return True

    def set_display_name(self, name, _cancellable):
        target = os.path.join(os.path.dirname(self.path), name)
        if os.path.exists(target):
            raise _error(Gio.IOErrorEnum.EXISTS, 'Target file exists')
        os.rename(self.path, target)
        return self._child(target)

    def delete(self, _cancellable):
        if os.path.isdir(self.path):
            if os.listdir(self.path):
                deleted_full_folders.append(self.path)
            shutil.rmtree(self.path)
        elif os.path.exists(self.path):
            os.remove(self.path)
        else:
            raise _error(Gio.IOErrorEnum.NOT_FOUND, 'No such file')
        return True

    def make_directory_with_parents(self, _cancellable):
        if os.path.isdir(self.path):
            raise _error(Gio.IOErrorEnum.EXISTS, 'File exists')
        os.makedirs(self.path)
        return True


def fake_new_for_path(path):
    return FakeFile(path)


class FakeMount:
    """What DeviceMonitor asks of a Gio.Mount."""

    def __init__(self, root, name):
        self.root = root
        self.name = name

    def is_shadowed(self):
        return False

    def get_root(self):
        return self.root

    def get_drive(self):
        return None

    def get_name(self):
        return self.name

    def can_eject(self):
        return True
