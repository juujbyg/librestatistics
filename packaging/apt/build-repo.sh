#!/usr/bin/env bash
# Build (or update) a signed flat apt repository.
#
#   packaging/apt/build-repo.sh REPO_DIR GPG_KEY_ID file.deb [more.deb ...]
#
# REPO_DIR ends up containing:
#   pool/main/*.deb                 every package version ever published
#   dists/stable/main/binary-all/   Packages, Packages.gz
#   dists/stable/{Release,InRelease,Release.gpg}
#   librestatistics.gpg                   public key users add to their keyring
#
# Users install with the snippet in README.md ("apt" section).
set -euo pipefail

repo=$1; key=$2; shift 2
mkdir -p "$repo/pool/main" "$repo/dists/stable/main/binary-all"
cp -f "$@" "$repo/pool/main/"

cd "$repo"
apt-ftparchive packages pool > dists/stable/main/binary-all/Packages
gzip -9kf dists/stable/main/binary-all/Packages

apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=librestatistics \
  -o APT::FTPArchive::Release::Label=librestatistics \
  -o APT::FTPArchive::Release::Suite=stable \
  -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Architectures=all \
  -o APT::FTPArchive::Release::Components=main \
  -o APT::FTPArchive::Release::Description="librestatistics apt repository" \
  release dists/stable > dists/stable/Release

gpg --batch --yes --local-user "$key" --clearsign -o dists/stable/InRelease dists/stable/Release
gpg --batch --yes --local-user "$key" -abs -o dists/stable/Release.gpg dists/stable/Release
gpg --batch --yes --export "$key" > librestatistics.gpg
touch .nojekyll
