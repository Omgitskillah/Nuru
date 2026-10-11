# Nuru for Obsidian

A sidebar for chatting with Nuru, running on this computer as `nuru-serve`, about the notes in this vault
([WP01](../../nuru/docs/obsidian-remote/01-local-obsidian-chat.md), milestones M3 and M4). It speaks
[protocol v1](../../nuru/docs/obsidian-remote/protocol-v1.md). Desktop only.

What it does:
- Pick a conversation or start one; history comes back from the service.
- Send a message. A note or a selection goes with it only when you press **Attach note** or
  **Attach selection**.
- Your messages show as bubbles on the right; Nuru's replies as Markdown across the panel, in the input box's
  font size.
- While a turn has nothing to show yet, its line says what Nuru is doing: getting ready (loading its model
  and tools, which only the first message after the service starts waits for) or thinking, with the
  seconds so far.
- Watch the turn: the reply as Markdown, the tools Nuru runs, and what each write actually did (done,
  not run, failed, or outcome unknown).
- Answer Nuru's questions before they expire. The service's clock decides, and no answer counts as No.
  Closing the panel never answers.
- **Stop** a turn. Closing the panel only stops following it, and reopening picks it up again.
- Note links open in Obsidian, web links in the browser. Replies are rendered with markdown-it, not
  Obsidian's renderer: raw HTML shows as text, and images and embeds show as links, so nothing in a reply
  runs or loads. Callouts, math and embeds therefore show as their text.

## Install

Needs Node 24 and npm, `uv`, and desktop Obsidian.

1. **Install the plugin into a vault**, from the Nuru folder:

   ```sh
   npm ci --prefix plugins/obsidian
   node plugins/obsidian/install.js <vault>
   ```

   It builds `dist/` and copies `main.js`, `manifest.json` and `styles.css` into
   `<vault>/.obsidian/plugins/nuru/`. It checks every path first, refuses symlinks, and swaps the files in
   only once all of them are copied. It never enables the plugin, and leaves the plugin's settings
   (`data.json`), the vault's other settings and its notes alone.
2. **Pair it with Nuru**: in the Nuru folder, `uv run nuru-serve token add obsidian`. This writes the
   plugin's token to `~/.nuru/service/clients/obsidian.token`, outside the vault, so it never syncs.
3. **Start the service**: `uv run nuru-serve`. It must serve this vault (`VAULT_PATH` in `.env.obsidian`):
   the plugin compares the vault's ID with the service's and won't chat otherwise. Installing the plugin
   doesn't change which vault the service serves. For another vault, such as a test one, set it for that
   run: `VAULT_PATH=<vault> uv run nuru-serve`.
4. **Enable it**: in Obsidian, Settings → Community plugins → **Nuru**. The lantern in the ribbon opens the
   chat. **Nuru** in the plugin's settings shows the port (27125 unless you change it), where the token is
   read from, this vault's ID, and **Check and reconnect**.

**Upgrade**: pull, then run step 1 again, and turn Nuru off and on (or restart Obsidian) to load the new
version. Settings, the token and the conversations stay; the conversations live in the service, not the
vault.

**Uninstall**: turn Nuru off under Community plugins, then delete `<vault>/.obsidian/plugins/nuru/` (its
`data.json` holds only the port and the last conversation's ID). To revoke its access too,
`uv run nuru-serve token remove obsidian`. The conversations stay in the service's database.

**Same build everywhere**: dependencies are pinned exactly (`package-lock.json`, installed with `npm ci`),
and the build checks that `manifest.json` and `package.json` carry the same version. Two builds from the
same commit are byte-identical: compare `shasum -a 256 dist/*`.

To try it without a model, `uv run --locked python nuru/tests/scripted_serve.py --dir <scratch>` serves a
scratch vault over scripted scenarios: a message such as `scenario:approval` picks one (see `SCENARIOS`).
Open `<scratch>/vault` in Obsidian, and use the printed token and port.

## Developing

- `npm test`: unit tests (`node --test`) over a stand-in `obsidian` module on jsdom (`test/helpers/`).
- `npm run test:e2e`: the client and turn follower against the real nuru-serve over scripted scenarios.
  Needs `uv` on PATH.
- `npm run build`: bundles `dist/` with esbuild.
- The icon (`lib/icon.js`) is `nuru/src/core/assets/lantern.png` traced to an SVG path with potrace.

Plain CommonJS, no TypeScript. esbuild's install script is turned off (`allowScripts`): its binary comes
from its platform package.

The build and test layout (esbuild bundling `main.js` to `dist/`, `node --test` with test helpers) follows
RemoteNotes' Obsidian plugin, branch `feat/obsidian-background-capture` at commit `04a2774ba9c7`
([source](https://github.com/Omgitskillah/RemoteNotes/tree/04a2774ba9c7/obsidian-plugin)). The installer
(`install.js`, `lib/install.js` and its tests) is adapted from that commit's; it also installs
`styles.css`. No other code is copied from it; its note writer and HTTP receiver are not used.
