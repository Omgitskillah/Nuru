Nuru for Obsidian on Windows and macOS. Install it with the BRAT plugin from this repository, then use **Set up everything** in the plugin's settings. It installs Nuru and Ollama, and downloads the models.

## What's new

- Models added in Settings appear in the model picker right away.
- Select, copy and paste text in chat. Press Up in an empty message box to recall earlier messages.
- Open the sources listed beneath a reply.
- Conversations get summarised titles.
- Find and rename conversations in History, move them to Trash, and empty Trash manually.
- See how full the context window is beside the model picker, with a notice when older messages were left out.
- Click the context ring to set memory use per model in 1 GB steps. Measure finds the largest context window verified on this computer; Apply reloads once. An interrupted load has an explicit recovery flow.

## What this release holds

- **`main.js`, `manifest.json`, `styles.css`:** the Obsidian plugin, which BRAT installs.
- **`nuru-<version>.zip`:** Nuru itself, at the exact commits of Nuru and its servers that this release was tagged at.
- **`SHA256SUMS`:** a checksum for each of the files above.

## Not in this release

- **The Python tool (`run_python`) is unavailable.** It runs scripts in a Docker sandbox, so it needs Docker installed and running. It also needs a developer setup: a `.env.python` file, and Rust to build its server. Without them, Nuru starts as usual, with every other tool.
- **Phone capture is off on Windows.** It works on macOS.
- **Context-memory measurement is for local Ollama models.** It needs exclusive use of the model runner and refuses while other models are resident. Measurement can take time; it does not run in the background automatically. Send a message with the model first: Measure starts from the model as it is loaded.

## What the checksums do and don't prove

- The checksums show that a download arrived complete and unchanged.
- They come from this same release, so they can't prove the release itself is genuine.
- The Nuru files aren't code-signed. Windows SmartScreen or an antivirus program may warn about them, or set them aside.
