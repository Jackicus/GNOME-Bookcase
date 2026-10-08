# Bookcase on Flathub

What is here, and the steps to submit it. Nothing has been sent to Flathub; every step below
that touches GitHub is yours to take.

## The files

| File | What it is |
| --- | --- |
| `io.github.jackicus.Bookcase.json` | Flathub's manifest: the release build of a git tag (tag and commit pinned) |
| `io.github.jackicus.Bookcase.Devel.json` | The development build of this checkout ("Bookcase (Development)"); GNOME Builder and CI use it |
| `poppler.json` | Poppler 26.10 with its GLib API and typelib (PDFs), and poppler-data (CJK PDFs); not in the runtime |
| `python3-lxml.json` | lxml from its PyPI sdist (made with flatpak-pip-generator, plus `--ignore-installed`: the SDK has lxml, the Platform does not) |
| `python3-speechd.json` | Speech Dispatcher's Python client (Read Aloud); the daemon is the host's |
| `lint-exceptions.json` | Waives `appid-url-not-reachable` until step 1 below; then delete it |

Runtime: `org.gnome.Platform//51` (GTK 4.24, libadwaita 1.10, WebKitGTK 2.54 with API 6.0,
Python 3.14, PyGObject 3.58, libsecret, libarchive's `bsdtar`, blueprint-compiler 0.22.2 in
the SDK). The sources carry `x-checker-data`, so Flathub's external data checker opens pull
requests when Poppler, poppler-data, lxml or speech-dispatcher release. The permissions and
their reasons are in `docs/decisions.md` ("The Flatpak sees the usual places; the file
chooser grants the rest"); paste that section to a reviewer who asks.

## Building and checking it locally

```sh
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51 org.gnome.Platform//51

# The release manifest (the tag), installed as io.github.jackicus.Bookcase
flatpak run org.flatpak.Builder --user --install --force-clean \
  --state-dir=build/flatpak/state --repo=build/flatpak/repo \
  build/flatpak/build-release build-aux/flatpak/io.github.jackicus.Bookcase.json

# The linters Flathub runs (the repo check also wants screenshots mirrored, which only
# Flathub's own build does: appstream-external-screenshot-url and
# appstream-screenshots-not-mirrored-in-ostree are expected locally)
lint() { flatpak run --command=flatpak-builder-lint org.flatpak.Builder "$@"; }
lint --exceptions --user-exceptions build-aux/flatpak/lint-exceptions.json \
  manifest build-aux/flatpak/io.github.jackicus.Bookcase.json
lint appstream build/flatpak/build-release/files/share/metainfo/io.github.jackicus.Bookcase.metainfo.xml
lint --exceptions --user-exceptions build-aux/flatpak/lint-exceptions.json repo build/flatpak/repo
```

When `flatpak run` complains of `/run/user/1000/doc/by-app/…`, the document portal is not
running: add `--no-documents-portal` after `flatpak run`.

Inside the sandbox, the screenshot scripts run on the installed build with
`BOOKCASE_PREFIX=/app` (scripts/harness.py), for example on a demo library made inside it:

```sh
flatpak run --no-documents-portal --filesystem="$PWD" --command=python3 \
  io.github.jackicus.Bookcase.Devel scripts/demo_library.py --data-dir "$PWD/build/flatpak/demo"
scripts/headless.sh flatpak run --no-documents-portal --filesystem="$PWD" \
  --env=BOOKCASE_PREFIX=/app --env=BOOKCASE_DATA_DIR="$PWD/build/flatpak/demo" \
  --command=python3 io.github.jackicus.Bookcase.Devel scripts/screenshot.py \
  build/flatpak/reader.png --read "The Glass Estuary"
```

The unit tests run in the sandbox on the SDK with `flatpak run --devel` over the checkout
(`meson setup` and `meson compile` first, for the widget tests' gresource). Run them in the
release app (`io.github.jackicus.Bookcase`), not the .Devel one: the tests' application IDs
are `io.github.jackicus.Bookcase.*Test`, and the Flatpak portal gives WebKit's web process a
bus name only under the sandbox's own app ID.
On 2026-10-08 (GNOME 51) the suite ran the same in the sandbox as natively except three
BookView tests (07 scripts, 08 large book as a file, 10 footnotes) and Read Aloud's page test,
whose books are in the test's `/tmp`: WebKit's web process runs in a sandbox of its own that
does not see the app's private `/tmp`. The same steps with a book in the home folder (the
large-book hand-over included) work in the installed app. `test_printing_draws_every_page`
hung both in and out of the sandbox and was left out.

## Submitting

1. **Make the app ID's URL real.** Flathub requires `https://github.com/jackicus/bookcase` to
   exist for `io.github.jackicus.Bookcase` (the linter's `appid-url-not-reachable`). Rename
   the repository from GNOME-Bookcase to **Bookcase** (GitHub, Settings, General, Repository
   name; GitHub redirects the old URLs and clones). Then, in one commit: the URLs in
   `data/io.github.jackicus.Bookcase.metainfo.xml.in` (homepage, bugtracker, help,
   vcs-browser, contribute, and the screenshots'), README.md, `build-aux/aur/PKGBUILD`, the
   `url` of the git source in `io.github.jackicus.Bookcase.json`, and `git remote set-url
   origin https://github.com/Jackicus/Bookcase.git`; delete `lint-exceptions.json` and the
   `--exceptions --user-exceptions …` arguments in `.github/workflows/ci.yml`.
   (Keeping the name would mean asking Flathub for a linter exception, which they rarely
   grant for this, or a new app ID such as `io.github.jackicus.GNOME-Bookcase`, which is
   worse.)
2. **Tag a release.** Add a `<release version="0.3.0" date="…">` with notes to the metainfo,
   bump `version` in meson.build, make sure the screenshot URLs name a tag that has those
   PNGs (they point at `v0.2.0` now; point them at the new tag if the screenshots changed),
   commit, tag `v0.3.0`, push the tag. In `io.github.jackicus.Bookcase.json` set `tag` to
   `v0.3.0` and `commit` to `git rev-parse v0.3.0^{commit}`. Build it and run the linters
   (above): all clean.
3. **Fork and branch.** Fork https://github.com/flathub/flathub on GitHub (all branches, not
   only master), then:

   ```sh
   git clone --branch=new-pr git@github.com:Jackicus/flathub.git
   cd flathub
   git checkout -b add-bookcase new-pr
   cp ../Bookcase/build-aux/flatpak/{io.github.jackicus.Bookcase,poppler,python3-lxml,python3-speechd}.json .
   git add . && git commit -m "Add io.github.jackicus.Bookcase"
   git push -u origin add-bookcase
   ```

   Only the release manifest and the three module files go; not the .Devel manifest, not
   `lint-exceptions.json`. No `flathub.json` is needed: nothing here is x86_64-only (only x86_64 has been built so far).
4. **Open the pull request** against `flathub/flathub`'s **new-pr** branch (not master),
   titled "Add io.github.jackicus.Bookcase", and fill in its checklist. Comment
   `bot, build` to have Flathub's builder try it; fix what it or a reviewer asks for on the
   same branch.
5. **Expect questions on**: the filesystem permissions (answer with the decisions.md
   section), `org.gtk.vfs.*` (e-readers over MTP and eject), Avahi (Library Sharing's DNS-SD
   advertisement, off unless sharing is on), and `xdg-run/speech-dispatcher` (Read Aloud).
6. **After it is merged**, Flathub makes `github.com/flathub/io.github.jackicus.Bookcase`
   and invites you to it. Updates are pull requests there (a new tag and commit in the
   manifest); the data checker opens the dependency updates. Verify the app on
   flathub.org (Developer portal, sign in with GitHub as Jackicus) so it shows as verified.

## What does not work in the Flatpak

- Calibre's `ebook-convert` is the host's and unreachable, so Convert… and sending to a device
  offer only EPUB to Kobo EPUB (kepub.py's), as on a system without Calibre.
- KOReader sync does not push just before suspend (logind's PrepareForSleep needs
  `--system-talk-name=org.freedesktop.login1`, a Flathub linter error); it pushes on its usual
  triggers.
- Folders outside the granted places are reached through the file chooser portal; their paths
  show as `/run/user/…/doc/…`, and the welcome does not find books there.
- StarDict dictionaries in the host's `/usr/share/stardict` are not seen (those in
  `~/.local/share/stardict` are).
