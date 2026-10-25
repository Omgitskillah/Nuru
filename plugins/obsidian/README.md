# Nuru for Obsidian

A sidebar for chatting with Nuru, running on this computer as `nuru-serve`, about the notes in this vault
([WP01](../../nuru/docs/obsidian-remote/01-local-obsidian-chat.md), milestones M3 and M4). It speaks
[protocol v1](../../nuru/docs/obsidian-remote/protocol-v1.md). Desktop only.

What it does:
- Pick a conversation or start one; history comes back from the service.
- Send a message with the arrow (or Enter). While Nuru answers, the arrow is a red square that stops the turn.
- Attach the open note with the paperclip. Text you select in a note goes with your next message by
  itself: the line above the message box names the note and how much is selected, or the size of a
  table selection, whose cells go as a Markdown table. It's sent once, and offered again when it changes.
- Pick the conversation's model and thinking level with the two small buttons beside the arrow. The
  list is the service's: supported local models, and Claude (marked ☁, cloud) when it has a key. Thinking
  shows only for a model that can think. A change goes with your next message; the conversation keeps it,
  and its whole history goes to the new model.
- Your messages show as bubbles on the right; Nuru's replies as Markdown across the panel, in the input box's
  font size.
- While a turn runs, its line says what Nuru is doing now, with the seconds so far and the tool calls so
  far: getting ready (loading its model and tools, which only the first message after the service
  starts waits for), thinking, running a tool, or writing the reply.
- Watch the turn: the reply as Markdown, and above it what Nuru did on the way, in order, in one section
  that is open while the turn runs and folds to **Thought (N seconds)** when it ends. Inside, each model
  call's thinking is folded under **Thought (N words)**. Each tool call shows ✓, ✗ or … with the note,
  path or query it was given (never its content), and why it failed; open it for every argument. A retry
  or steer gets its own line.
  Older turns from history keep only their tools and last thinking: **Show all steps** fetches the rest
  (events are kept 30 days after a turn ends).
- What each write actually did, at the end of that section: done, not run, failed, not written (refused
  before it wrote anything), or outcome unknown. When a tool wrote more than once in a turn, each says
  which call it was ("write_note, 2nd call, nuru/giulia-bona.md: done"). Under the reply, **Vaulted**,
  a section that folds like Thought and starts open, links every note a write created or updated. A write
  whose outcome is unknown is shown under the reply too, since that note needs checking.
- Under the header, the service's status: the vault, the model (not loaded yet, loading, ready,
  unavailable), the notes server, and turns running or waiting. It refreshes every 10 seconds.
- Answer Nuru's questions before they expire. The service's clock decides, and no answer counts as No.
  Closing the panel never answers.
- **Stop** a turn with the red square. Closing the panel only stops following it, and reopening picks it up again.
- Note links open in Obsidian, web links in the browser. Replies are rendered with markdown-it, not
  Obsidian's renderer: raw HTML shows as text, and images and embeds show as links, so nothing in a reply
  runs or loads. Callouts, math and embeds therefore show as their text.

## Install from a release

Needs only desktop Obsidian, on Windows or macOS.

1. Install the BRAT plugin, and add the beta plugin `Omgitskillah/Nuru`.
2. Turn Nuru on under Community plugins and open its settings.
   - **Set up everything** runs the steps below that are still to do, in order.
   - Each step can also be run on its own, once the step before it is done.
3. **1. Nuru: Set up Nuru.**
   - It downloads this release's Nuru, uv and calculator, and checks each against the sha256 built into the plugin.
   - It installs them into `~/.nuru/app/<version>/<n>/`, then installs Python and Nuru's packages there.
   - It switches `~/.nuru/install.json` to the new install only once that install starts.
   - **Repair** installs again, beside the install in use. Settings (`.env.*`) carry over, and the previous install is kept to roll back to.
4. **2. Ollama:** Ollama at the version this release pins, or newer. Nothing is downgraded.
   - **Install Ollama** downloads it and checks it against the pinned sha256.
     - On Windows, it runs Ollama's own installer silently, for this user only, with no admin rights (1.5 GB).
     - On macOS, it unpacks `Ollama.app` into `~/Applications` (200 MB) and opens it.
   - **Update Ollama** on Windows does the same over an older install. On macOS, an older Ollama updates from its own menu-bar icon.
   - **Start Ollama** opens an installed Ollama that isn't running.
5. **3. Models:** **Download** fetches the models Nuru needs through Ollama.
   - Those are the chosen chat model (`qwen3.5:4b` unless changed) and `nomic-embed-text:v1.5`, for search.
6. **4. Ready:** open the Nuru panel to chat.

## Install from a checkout

Needs Node 24 and npm, `uv`, and desktop Obsidian.

1. **Install the plugin into a vault**, from the Nuru folder:

   ```sh
   npm ci --prefix plugins/obsidian
   node plugins/obsidian/install.js <vault>
   ```

   It builds `dist/` and copies `main.js`, `manifest.json` and `styles.css` into
   `<vault>/.obsidian/plugins/nuru/`. It checks every path first, refuses symlinks, and swaps the files in
   only once all of them are copied. It never enables the plugin, and leaves the plugin's settings
   (`data.json`), the vault's other settings and its notes alone. It also records the Nuru folder and
   `uv` in `~/.nuru/install.json`, outside every vault, for the plugin to start Nuru with, along with
   this shell's `PATH`: Obsidian opened from the Dock gets only the system folders, and Nuru's tools run
   through `uv` and `cargo`. So run it from a terminal where `uv` and `cargo` work.
2. **Enable it**: in Obsidian, Settings → Community plugins, refresh the list and turn on **Nuru**. The
   lantern in the ribbon opens the chat.

That's all. Opening the panel starts Nuru for this vault if it isn't running ("Starting Nuru…"; the first
time can take a minute while `uv` builds Nuru's environment), and pairs the plugin first if it has no
token yet. A Nuru the plugin started stops when Obsidian quits, or when the plugin is turned off. Its
output goes to `~/.nuru/service/serve.log`. Loading the model is a separate step: the first message
loads it.

- **Starting it yourself instead**: turn off **Start Nuru when the panel opens** in the plugin's settings,
  and run `uv run nuru-serve` in the Nuru folder. The panel then shows a **Start Nuru** button when it
  isn't running. A Nuru started from a terminal (or at login) is never stopped or switched by the plugin.
- **Another vault**: Nuru serves one vault at a time. If it's serving another vault, the panel says which
  and offers **Switch Nuru to this vault**. That works only for a Nuru the plugin started, and only while
  that vault has no turn running or waiting.
- **The plugin's settings** show the port (27125 unless you change it), where the token is read from,
  this vault's ID (`.nuru/vault-id`, which Nuru writes the first time it serves the vault, and which
  stays the same if you move or rename the vault), and **Check and reconnect**.
- **Nuru's own settings** are there too, under **Nuru**: the default model (from the models Nuru can
  run), how long the local model stays loaded, and the Claude API key. They're read from and written to
  the env files in the Nuru folder (`NURU_BASE_MODEL`, `NURU_MAIN_MODEL` and `NURU_KEEP_ALIVE` in
  `.env.nuru`, `CLAUDE_API_KEY` in `.env.deep-think`), the same lines Nuru reads, so editing a file works
  as well; a change rewrites only that line. Nothing goes into `data.json`, which syncs with the vault.
  The key is shown only as a fingerprint. **Check** asks Anthropic's free model list whether it works;
  the tab sends the key nowhere until you click it. **Restart Nuru** applies a change (for a Nuru the
  plugin started; restart one from a terminal there). Every other setting stays in the files.
- **A copied vault**: Nuru won't serve a copy of a vault while the original still exists, since both
  would claim the same history. Settle it in the Nuru folder: `uv run nuru-serve vault rebind <vault>`
  if this copy is the one to keep, or `uv run nuru-serve vault clone <vault>` to make it a separate vault.
- **The plugin needs a Nuru from WP04 M1 or later.** Against an older one it says so: `git pull` in the
  Nuru folder, then restart Nuru.
- Why the Nuru folder isn't a plugin setting: plugin settings live in the vault and sync with it, so a
  synced or shared vault could point the plugin at a folder of its own and have its code run when the
  vault opens. `~/.nuru/install.json` is only on this computer.

## Upgrade

Leave Obsidian open and `nuru-serve` running.

1. Get the new code: `git pull` in the Nuru folder.
2. Install over the old copy, from the Nuru folder:

   ```sh
   npm ci --prefix plugins/obsidian
   node plugins/obsidian/install.js <vault>
   ```

   It prints `Installed: <vault>/.obsidian/plugins/nuru`. Obsidian keeps running the old version until
   you reload the plugin. It also rewrites `~/.nuru/install.json` with this shell's `PATH`. **An install
   from before that was recorded must run this step again**, from a terminal where `uv` and `cargo`
   work. Otherwise a Nuru started from Obsidian can't find `cargo`, and the first message fails with
   "Nuru couldn't start its model and tools" (`~/.nuru/service/serve.log` says why).
3. Reload it: in Obsidian, Settings → Community plugins, turn **Nuru** off, then on again. Restarting
   Obsidian does the same.
4. Check: Settings → Community plugins shows the new version under **Nuru**. Open the chat from the
   lantern. The last conversation is still there, and the port in Nuru's settings is unchanged.

Nothing else changes: the port and last conversation (`data.json`), the token (outside the vault) and the
conversations (in the service's database) all stay. To try an upgrade with no new code, skip step 1. The
version stays the same, but steps 2–4 still replace the files and reload them.

## Uninstall

1. In Obsidian, Settings → Community plugins, turn **Nuru** off. The lantern leaves the ribbon.
2. Delete the folder `<vault>/.obsidian/plugins/nuru/`. **Nuru** disappears from Community plugins
   (reopen Settings if it's still listed). The folder holds only the plugin's files and `data.json` (the
   port and the last conversation's ID). Your notes are elsewhere and aren't touched.
3. Optional: stop the plugin from connecting again with `uv run nuru-serve token remove obsidian`, in the
   Nuru folder.

The conversations stay in the service's database. After installing again (Install, steps 1 and 4), you can
pick them in the panel. After step 3 you also need to pair again (Install, step 2).

**Same build everywhere**: dependencies are pinned exactly (`package-lock.json`, installed with `npm ci`),
and the build checks that `manifest.json` and `package.json` carry the same version. Two builds from the
same commit are byte-identical: compare `shasum -a 256 dist/*`.

To try it without a model, serve a scratch vault over scripted scenarios:

1. In the Nuru folder, start the scripted server and leave it running:
   `uv run --locked python nuru/tests/scripted_serve.py --dir <scratch> --port 27126`.
   Without `--port` it takes a free port, a different one each time.
2. Give the plugin its token, which is new each time the server starts:
   `cp <scratch>/service/clients/obsidian.token ~/.nuru/service/clients/obsidian.token`.
   This replaces the token for the real `nuru-serve`.
3. Open `<scratch>/NuruPluginTestVault` in Obsidian, set the plugin's **Port** to 27126 and click **Check and reconnect**.
4. In the Nuru panel, send a scenario's name as the message, such as `scenario:approval` (see `SCENARIOS`);
   any other text is echoed back. `scenario:activity` shows every kind of step: streamed thinking, a failed
   and a working call, a steer and a silent retry.

Afterwards, set the port back to 27125 and make a new token for the real service with
`uv run nuru-serve token add obsidian`.

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
