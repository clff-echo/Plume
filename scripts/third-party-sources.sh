#!/usr/bin/env bash
# Include the exact Spotifyd source tree and all locked Rust dependencies.
set -euo pipefail
mkdir -p build/third-party dist
curl --fail --location --retry 3 https://github.com/Spotifyd/spotifyd/archive/refs/tags/v0.4.2.tar.gz -o build/third-party/spotifyd.tar.gz
tar -xzf build/third-party/spotifyd.tar.gz -C build/third-party
cd build/third-party/spotifyd-0.4.2
mkdir -p .cargo
cargo vendor --locked vendor > .cargo/config.toml
cat > PLUME-BUILD.txt <<'EOF'
Upstream Spotifyd v0.4.2, unmodified. Plume ships the official Linux default release.
Install Rust stable and the build dependencies listed in README.md.
The release workflow in .github/workflows/ is part of this source tree.
To build offline with the vendored dependencies:
  cargo build --release --locked --offline
See Cargo.toml for feature selection and upstream release workflow for target flags.
EOF
cd ..
tar -czf ../../dist/Spotifyd-0.4.2-corresponding-source.tar.gz spotifyd-0.4.2
