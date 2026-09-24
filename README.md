# portless-manager

**English** | [한국어](README.ko.md)

A macOS menu bar for [portless](https://github.com/vercel-labs/portless) dev servers.
See every portless project on your machine grouped by folder, and start, stop, or open them in one click.

```
🖥 2
├ Proxy 🟢 running · 443          ▸ stop · doctor · list
├ ■ Stop all services (2)
├ Prune orphaned processes
├ work
│  └ ⚪ api-server                  ▸ ▶ Start · Copy URL · Editor · Terminal · Finder · Log
└ personal
   ├ 🟢 blog   blog.localhost   🌿 1/2
   │   ▸ ↗ https://blog.localhost · ■ Stop · ⟳ Restart · …
   │   ▸ worktree
   │       🟢 feat/search   search.blog.localhost   ▸ …
   │       ⚪ fix/nav                              ▸ …
   └ ⛔ docs                       ▸ Not runnable — no "dev" script
```

## Features

- **Auto-discovery.** Rescans your project folders every 10 seconds. New projects, renamed apps, and
  added or removed git worktrees show up without touching the plugin.
- **Worktrees under their project.** Each worktree gets its own status and actions, with the same
  `<branch>.<name>.localhost` hostname portless assigns.
- **Live status from portless itself.** Reads `~/.portless/routes.json`, so services you started
  from a terminal show up too.
- **Start / stop / restart.** Starts are detached from the menu bar. Stops terminate the whole
  process tree so `next dev` doesn't linger on a port.
- **Shortcuts.** Open the page, copy the URL, open the folder in your editor, terminal, or Finder,
  and view the service log.
- **Multilingual.** English and Korean, picked from your macOS language. Adding a language is one JSON file.
- **Proxy controls.** Start and stop the proxy, `portless doctor`, `portless prune`, and
  install the proxy as a startup service.

## Requirements

- macOS with [SwiftBar](https://github.com/swiftbar/SwiftBar)
- [portless](https://github.com/vercel-labs/portless) installed globally (`npm i -g portless`),
  which needs Node.js 24+
- Python 3.10+ (standard library only, no packages to install)

Tested with portless 0.15.5 and SwiftBar 2.1.1.

## Install

```sh
git clone https://github.com/SiyeolBaek/portless-manager.git
cd portless-manager
python3 -m portless_manager install
```

This writes `portless-manager.10s.sh` into your SwiftBar plugin folder. The plugin is a thin wrapper
that runs this checkout, so `git pull` is all an update needs. Run `install` again only if you move
the checkout.

### Start the proxy first

portless serves on port 443, which requires `sudo`. SwiftBar has no terminal to ask for a password,
so **Start proxy** in the menu opens a Terminal window to run it. To skip this step on every boot,
choose **Start at boot** once, which runs `portless service install`.

While the proxy is down, the menu disables starting services. A launch would fail right away anyway.

## Configuration

Tell it where your projects live in `~/.config/portless-manager/config.json`:

```json
{
  "roots": ["~/Documents/work", "~/Documents/personal"],
  "language": "auto"
}
```

- Each **direct child** of a root is a project candidate. The menu groups projects by root folder name.
- A folder counts as a portless project if it has a `portless.json`, or if its `package.json` lists
  `portless` in its dependencies or scripts.
- Without a config file, it scans whichever of `~/Developer`, `~/Projects`, `~/Code`, `~/src`, and
  `~/dev` exist.
- `language` is optional: `auto` (default), `en`, `ko`, … See [Languages](#languages).
- Set `PORTLESS_MANAGER_CONFIG` to use a different config path. `PORTLESS_STATE_DIR` is honored the
  same way portless honors it.

```sh
python3 -m portless_manager config   # show the config path and resolved roots
python3 -m portless_manager list     # print every project and its status
```

## Languages

Menus, notifications, and CLI output are translated. Available: **English** (`en`), **한국어** (`ko`).

The language is chosen in this order:

1. The `PORTLESS_MANAGER_LANG` environment variable
2. `"language"` in `config.json`
3. Your macOS preferred language (`defaults read -g AppleLanguages`)
4. `LC_ALL` / `LANG`
5. English

A regional tag falls back to its base language (`ko-KR` → `ko`), and any string missing from a
translation falls back to English. SwiftBar usually doesn't pass `LANG` to plugins, which is why the
macOS setting comes before it. For one-off CLI use, pass `--lang`:

```sh
python3 -m portless_manager --lang en list
```

### Adding a language

1. Copy `portless_manager/locales/en.json` to `portless_manager/locales/<code>.json`, using a
   language code such as `ja` or `pt-BR`.
2. Translate the values. Keep every key and every `{placeholder}` as it is. Symbols like ▶ ■ ⟳ are
   part of the label, so keep them too.
3. Set `"_language"` to the language's own name.
4. Run `python3 -m unittest discover -s tests`. It fails if a key or placeholder is missing.

Pull requests with new languages are welcome.

## Status icons

| Icon | Meaning |
|---|---|
| 🟢 | Running. portless has a live route for it. |
| ⏳ | Starting. Launched from the menu, but no route yet. |
| ⚠️ | Failed. The process exited before registering a route. Check **Log**. |
| ⚪ | Stopped |
| ⛔ | Not runnable. No script to run, or a monorepo `apps` config. |

## How it works

| Action | What happens |
|---|---|
| Status | Matches each project's computed hostname against live entries in `~/.portless/routes.json` (`{hostname, port, pid}`). `portless list` has no JSON output, so it reads the file directly. |
| Start | Runs bare `portless` in the project folder, in a new session so it outlives SwiftBar. Output goes to `~/Library/Logs/portless-manager/<name>.log`. |
| Stop | Sends SIGTERM to the route's pid (the portless CLI), which shuts the app down and removes the route. If it's still alive after 8 seconds, it SIGKILLs the child process groups too. |
| Stop all | Stops every live route, including ones outside your configured roots. |

Launch records live in `~/Library/Application Support/portless-manager/launch/`. They're how the menu
tells "starting" apart from "failed".

## Limitations

- **Naming rules are copied from portless.** Hostnames follow portless 0.15.5's `inferProjectName`
  and `detectWorktreePrefix`. If a future portless release changes them, a running service may show
  as stopped. `tests/` pins the current rules.
- **Monorepos aren't supported.** Projects with `apps` in `portless.json` are listed but not started.
- **Custom names don't match.** Services started by hand with `--name` get a different hostname than
  the computed one.
- **Worktree name collisions.** A worktree on `main` or `master` gets no prefix, so it shares a
  hostname with the main checkout. The menu flags this.

## Development

All user-facing text lives in `portless_manager/locales/`. Code refers to keys only
(`_("target.start")`), and a test fails if a hard-coded Korean string slips into the code outside comments and docstrings.

```sh
python3 -m unittest discover -s tests
python3 -m portless_manager render --wrapper /dev/null   # print the raw SwiftBar output
```

## License

[MIT](LICENSE) © Siyeol Baek
