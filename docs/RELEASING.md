# Releasing LibreStatistics

One tag builds everything. The GitHub release always gets these files:

| File | Built by | Built on |
|---|---|---|
| `librestatistics-X.Y.Z-py3-none-any.whl`, `librestatistics-X.Y.Z.tar.gz` | `python -m build` | ubuntu |
| `librestatistics_X.Y.Z_all.deb` | `dpkg-buildpackage` (`debian/`) | ubuntu-24.04 |
| `librestatistics_X.Y.Z_amd64.snap` | snapcraft (`snap/snapcraft.yaml`) | ubuntu |
| `librestatistics-X.Y.Z-windows-x64.zip`, `…-setup.exe` | PyInstaller + Inno Setup (`packaging/windows/`) | windows |

Publishing to PyPI, the Snap Store and the apt repository is optional. Each one switches on once it is set up (see below).

## Cutting a release

```bash
python3 scripts/bump_version.py 2.1.0 "What changed, in one line"
git commit -am "Release 2.1.0"
git tag v2.1.0
git push --follow-tags
```

`.github/workflows/release.yml` checks that the tag matches the package version, builds every package, smoke-tests the `.deb` and the `.exe` files, and creates the release.

## One-time setup per channel

All of these live under **Settings → Secrets and variables → Actions** in the GitHub repo.

### GitHub Releases
Nothing to set up.

### Snap Store
1. Register the name: `snapcraft register librestatistics` (needs an Ubuntu One account).
2. Export credentials: `snapcraft export-login --snaps=librestatistics --channels=stable -`
3. Add the output as the secret **`SNAPCRAFT_STORE_CREDENTIALS`**.
4. The observe interfaces (`system-observe`, `hardware-observe`, `mount-observe`, `network-observe`) aren't auto-connected. Users connect them with `snap connect` (see README). You can also request auto-connection on the [Snapcraft forum](https://forum.snapcraft.io/c/store-requests/19).

### apt repository (GitHub Pages)
1. Create a signing key with no passphrase, used only for this repository:
   ```bash
   gpg --batch --passphrase '' --quick-gen-key "librestatistics apt <147175691+juujbyg@users.noreply.github.com>" ed25519 sign never
   gpg --armor --export-secret-keys "librestatistics apt" > apt-key.asc   # keep this file private
   ```
2. Add the contents of `apt-key.asc` as the secret **`APT_GPG_PRIVATE_KEY`**, then delete the file.
3. Add the variable **`PUBLISH_APT`** = `true`.
4. After the first release has run, set **Settings → Pages → Source** to *Deploy from a branch*, branch `gh-pages`, folder `/`.

The repository is served at `https://juujbyg.github.io/librestatistics/apt`. Every release adds its `.deb` to `pool/`, so older versions stay installable.

A Launchpad PPA is an alternative. It requires uploading signed *source* packages (`debuild -S`, then `dput ppa:juujbyg/librestatistics`) from a machine with your Launchpad GPG key, and it is not automated here.

### PyPI (`pip install librestatistics`, also works on Windows/macOS)
1. On pypi.org, add a **pending trusted publisher**: project `librestatistics`, owner `juujbyg`, repo `librestatistics`, workflow `release.yml`, environment `pypi`.
2. In GitHub, create the environment **`pypi`** (Settings → Environments).
3. Add the variable **`PUBLISH_PYPI`** = `true`.

### Windows
The release includes a portable zip and an installer (`…-setup.exe`). The installer adds the commands to PATH and creates Start-menu shortcuts. Neither is code-signed, so Windows SmartScreen will warn on first run; signing requires buying a certificate.

To list librestatistics in **winget**, submit a manifest pointing at the release's `-setup.exe` to [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs). `wingetcreate new <installer-url>` generates the manifest.

## Building locally

```bash
python3 -m build                                   # wheel + sdist
dpkg-buildpackage -us -uc -b                       # .deb (needs debhelper, dh-python, pybuild-plugin-pyproject)
snapcraft                                          # .snap (needs snapcraft + LXD)
pwsh packaging/windows/build.ps1                   # Windows .exe/.zip/installer (on Windows)
```
