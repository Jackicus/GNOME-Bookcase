# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

# An older blueprint-compiler on PATH (as some distributions ship) falls back to the wrap's.
blueprint_compiler = find_program('blueprint-compiler', version: '>= 0.22', required: false)
if not blueprint_compiler.found()
  # Fall back to the wrap. Its configured wrapper can't run uninstalled
  # (it can't find its own module), so run the source tree's script instead.
  subproject('blueprint-compiler')
  blueprint_compiler = find_program(
    meson.project_source_root() / 'subprojects' / 'blueprint-compiler' / 'blueprint-compiler.py',
  )
endif

# One target per .blp, each naming its .ui, so an edited .blp is recompiled. The .ui files
# land flat in the build directory, which the gresource lists them from.
blueprints = []
foreach blueprint : files(
    'window.blp',
    'reader_window.blp',
    'pages/home.blp',
    'pages/books.blp',
    'pages/duplicates.blp',
    'pages/groups.blp',
    'pages/book.blp',
    'pages/device.blp',
    'pages/discover.blp',
    'pages/catalog.blp',
    'pages/stats.blp',
    'dialogs/edit_metadata.blp',
    'dialogs/fetch_metadata.blp',
    'dialogs/add_books.blp',
    'dialogs/shelf.blp',
    'dialogs/preferences.blp',
    'dialogs/catalog_entry.blp',
    'dialogs/add_catalog.blp',
  )
  blueprints += custom_target(
    input: blueprint,
    output: '@BASENAME@.ui',
    command: [blueprint_compiler, 'compile', '--output', '@OUTPUT@', '@INPUT@'],
  )
endforeach

python3 = python.find_installation('python3')

# The metainfo data/ generates, under a name of its own in the gresource: the About dialog is
# built from it (its release notes included).
metainfo_resource = custom_target(
  input: metainfo_file,
  output: 'metainfo.xml',
  command: [python3, '-c', 'import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])',
            '@INPUT@', '@OUTPUT@'],
)

gnome.compile_resources(
  'bookcase',
  'bookcase.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
  dependencies: blueprints + [metainfo_resource],
)

configure_file(
  input: 'bookcase.in',
  output: 'bookcase',
  configuration: {
    'PYTHON': python3.full_path(),
    'VERSION': version,
    'APP_ID': application_id,
    'BASE_ID': base_id,
    'PROFILE': get_option('profile'),
    'localedir': get_option('prefix') / get_option('localedir'),
    'pkgdatadir': pkgdatadir,
  },
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr-xr-x',
)

install_data(
  '__init__.py',
  'annotations.py',
  'bulk_metadata.py',
  'calibre.py',
  'converting.py',
  'covers.py',
  'devices.py',
  'existing_books.py',
  'exporting.py',
  'i18n.py',
  'importing.py',
  'kepub.py',
  'kosync.py',
  'library.py',
  'lookup.py',
  'mail.py',
  'main.py',
  'online.py',
  'opds.py',
  'passwords.py',
  'pdf_location.py',
  'reader_sync.py',
  'reader_window.py',
  'reading.py',
  'schema.py',
  'search.py',
  'shortcuts.py',
  'speech.py',
  'sidebar.py',
  'stats.py',
  'titles.py',
  'window.py',
  install_dir: moduledir,
)

install_data(
  'formats/__init__.py',
  'formats/comic.py',
  'formats/epub.py',
  'formats/fb2.py',
  'formats/mobi.py',
  'formats/pdf.py',
  'formats/txt.py',
  install_dir: moduledir / 'formats',
)

install_data(
  'pages/__init__.py',
  'pages/actions.py',
  'pages/book.py',
  'pages/catalog.py',
  'pages/books.py',
  'pages/duplicates.py',
  'pages/device.py',
  'pages/discover.py',
  'pages/groups.py',
  'pages/home.py',
  'pages/stats.py',
  install_dir: moduledir / 'pages',
)

install_data(
  'dialogs/__init__.py',
  'dialogs/about.py',
  'dialogs/add_books.py',
  'dialogs/add_catalog.py',
  'dialogs/catalog_entry.py',
  'dialogs/bulk_metadata.py',
  'dialogs/edit_metadata.py',
  'dialogs/fetch_metadata.py',
  'dialogs/goals.py',
  'dialogs/highlights.py',
  'dialogs/kindle_mail.py',
  'dialogs/note.py',
  'dialogs/preferences.py',
  'dialogs/quick_open.py',
  'dialogs/send.py',
  'dialogs/shelf.py',
  'dialogs/shortcuts.py',
  'dialogs/sync_prefs.py',
  install_dir: moduledir / 'dialogs',
)

install_data(
  'widgets/__init__.py',
  'widgets/book_item.py',
  'widgets/book_tile.py',
  'widgets/book_view.py',
  'widgets/charts.py',
  'widgets/cover.py',
  'widgets/goal_card.py',
  'widgets/lookup_popover.py',
  'widgets/markup.py',
  'widgets/pdf_view.py',
  'widgets/rating.py',
  'widgets/remote_cover.py',
  'widgets/read_aloud.py',
  'widgets/util.py',
  install_dir: moduledir / 'widgets',
)

# Byte-compile the modules once they are installed (after every install_data above): they live
# outside site-packages, so Meson's python.bytecompile never reaches them, and a system install
# without bytecode imports twice as slowly.
bytecompile = get_option('python.bytecompile')
if bytecompile >= 0
  meson.add_install_script(
    python3,
    meson.project_source_root() / 'build-aux' / 'meson' / 'compile-python.py',
    get_option('datadir') / meson.project_name() / 'bookcase',
    bytecompile.to_string(),
    install_tag: 'runtime',
  )
endif
