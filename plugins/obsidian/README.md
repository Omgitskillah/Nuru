# Nuru for Obsidian

A sidebar for chatting with Nuru, running on this computer as `nuru-serve`, about the notes in this vault
([WP01](../../nuru/docs/obsidian-remote/01-local-obsidian-chat.md), milestone M3). It speaks
[protocol v1](../../nuru/docs/obsidian-remote/protocol-v1.md). Desktop only.

What it does:
- Pick a conversation or start one; history comes back from the service.
- Send a message. A note or a selection goes with it only when you press **Attach note** or
  **Attach selection**.
- Watch the turn: the reply as Markdown, the tools Nuru runs, and what each write actually did (done,
  not run, failed, or outcome unknown).
- Answer Nuru's questions before they expire. The service's clock decides, and no answer counts as No.
  Closing the panel never answers.
- **Stop** a turn. Closing the panel only stops following it, and reopening picks it up again.
- Note links open in Obsidian, web links in the browser. Raw HTML in a reply shows as text, and nothing
  in it runs.

## Setting up (until M4's installer)

1. In the Nuru folder: `uv run nuru-serve token add obsidian`. This writes the plugin's token to
   `~/.nuru/service/clients/obsidian.token`, outside the vault, so it never syncs.
2. Start the service: `uv run nuru-serve`. It must serve this vault: the plugin compares the vault's ID
   with the service's and won't chat otherwise.
3. Build and copy: `npm ci && npm run build` here, then copy `dist/` to `<vault>/.obsidian/plugins/nuru/`
   and enable **Nuru** under Community plugins.

To try it without a model, `uv run --locked python nuru/tests/scripted_serve.py --dir <scratch>` serves a
scratch vault over scripted scenarios: a message such as `scenario:approval` picks one (see `SCENARIOS`).
Open `<scratch>/vault` in Obsidian, and use the printed token and port.

## Developing

- `npm test`: unit tests (`node --test`) over a stand-in `obsidian` module on jsdom (`test/helpers/`).
- `npm run test:e2e`: the client and turn follower against the real nuru-serve over scripted scenarios.
  Needs `uv` on PATH.
- `npm run build`: bundles `dist/` with esbuild.

Plain CommonJS, no TypeScript. esbuild's install script is turned off (`allowScripts`): its binary comes
from its platform package.

The build and test layout (esbuild bundling `main.js` to `dist/`, `node --test` with test helpers) follows
RemoteNotes' Obsidian plugin, branch `feat/obsidian-background-capture` at commit `04a2774ba9c7`
([source](https://github.com/Omgitskillah/RemoteNotes/tree/04a2774ba9c7/obsidian-plugin)). No code is
copied from it; its note writer and HTTP receiver are not used.
