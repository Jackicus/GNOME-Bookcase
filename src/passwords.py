# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Passwords in the desktop's keyring (the Secret Service, through libsecret), never in
GSettings.

    keyring = passwords.Keyring('io.github.jackicus.Bookcase.Mail')   # a schema per use
    keyring.store(account, label, password)   # True when stored; KeyringError otherwise
    keyring.lookup(account)                   # the password, or None
    keyring.clear(account)                    # forget it (nothing when there was none)
    passwords.MemoryKeyring()                 # the same calls, in a dict (tests, demos)

`account` is a string naming whose password it is ('ada@example.org on smtp.example.org');
it is the schema's one attribute, `account`. Every call talks to the keyring over D-Bus and
may wait on it (an unlock prompt): call them from a thread. Without libsecret's typelib, or
with no Secret Service running, store() raises KeyringError and lookup() returns None.
"""

import logging
import threading
from gettext import gettext as _

log = logging.getLogger(__name__)

MAIL_SCHEMA = 'io.github.jackicus.Bookcase.Mail'


class KeyringError(Exception):
    """The keyring could not be used; str(error) is a sentence for the user."""


def _secret():
    try:
        import gi

        gi.require_version('Secret', '1')
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None
    return Secret


class Keyring:
    """Passwords under one libsecret schema; see the module."""

    def __init__(self, schema_name):
        self.schema_name = schema_name
        self._schema = None
        self._lock = threading.Lock()

    def _get_schema(self):
        Secret = _secret()
        if Secret is None:
            raise KeyringError(_('The keyring cannot be used: libsecret is not installed'))
        with self._lock:
            if self._schema is None:
                self._schema = Secret.Schema.new(
                    self.schema_name, Secret.SchemaFlags.NONE,
                    {'account': Secret.SchemaAttributeType.STRING})
        return Secret, self._schema

    def store(self, account, label, password):
        Secret, schema = self._get_schema()
        try:
            return Secret.password_store_sync(schema, {'account': account},
                                              Secret.COLLECTION_DEFAULT, label, password, None)
        except Exception as error:  # GLib.Error from D-Bus: no keyring, refused unlock
            log.warning('storing a password: %s', error)
            raise KeyringError(_('The password could not be saved in the keyring')) from error

    def lookup(self, account):
        try:
            Secret, schema = self._get_schema()
            return Secret.password_lookup_sync(schema, {'account': account}, None)
        except KeyringError:
            return None
        except Exception as error:
            log.warning('looking up a password: %s', error)
            return None

    def clear(self, account):
        try:
            Secret, schema = self._get_schema()
            Secret.password_clear_sync(schema, {'account': account}, None)
        except KeyringError:
            return
        except Exception as error:
            log.warning('clearing a password: %s', error)


class MemoryKeyring:
    """A keyring in a dict, with Keyring's calls (tests, screenshots)."""

    def __init__(self, passwords=None):
        self.passwords = dict(passwords or {})

    def store(self, account, label, password):
        self.passwords[account] = password
        return True

    def lookup(self, account):
        return self.passwords.get(account)

    def clear(self, account):
        self.passwords.pop(account, None)
