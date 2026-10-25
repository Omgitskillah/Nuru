Nuru for Obsidian on Windows and macOS. Install it with the BRAT plugin from this repository, then use **Set up everything** in the plugin's settings. It installs Nuru and Ollama, and downloads the models.

## What this release holds

- **`main.js`, `manifest.json`, `styles.css`:** the Obsidian plugin, which BRAT installs.
- **`nuru-<version>.zip`:** Nuru itself, at the exact commits of Nuru and its servers that this release was tagged at.
- **`SHA256SUMS`:** a checksum for each of the files above.

## Not in this release

- **The Python tool (`run_python`) is unavailable.** It runs scripts in a Docker sandbox, so it needs Docker installed and running. It also needs a developer setup: a `.env.python` file, and Rust to build its server. Without them, Nuru starts as usual, with every other tool.
- **Phone capture is off on Windows.** It works on macOS.

## What the checksums do and don't prove

- The checksums show that a download arrived complete and unchanged.
- They come from this same release, so they can't prove the release itself is genuine.
- The Nuru files aren't code-signed. Windows SmartScreen or an antivirus program may warn about them, or set them aside.
