# Mozart App Store

Community app store for umbrelOS.

## Apps

### UPS Monitor

Live UPS dashboard for your Umbrel — battery charge, estimated runtime, load and power status, powered by Network UPS Tools (NUT) and [PeaNUT](https://github.com/Brandawg93/PeaNUT).

Preconfigured for the CyberPower VP1200ELCD. Other NUT-supported USB HID UPS models will generally work as-is.

### OpenMuse

A personal agent with a browser, terminal, and files — [CopilotKit OpenMuse](https://github.com/CopilotKit/openmuse) (alpha), with a Playwright browser worker and persistent Chromium profiles.

Ships in sample mode (fictional data, no model key needed); add a model provider key to enable the real agent. Images are built by this repo's GitHub Actions from upstream commit `ef8f608` — see `mozart-openmuse/docker/README.md`.

### WP Sandbox

A self-contained WordPress site (WordPress 7.1.2 + MariaDB 11.5) for hosting local copies of websites on your Umbrel — backups, staging and testing.

Self-installing on first boot: it creates its database, admin account and permalinks, then prints a one-time magic sign-in link into its logs. A bundled WP-CLI companion service handles in-app maintenance, and the optional **Backups Folder** mount gives the app read-only access to a folder on your Umbrel for importing backups.

### WP Command

The command center for the WordPress copies on your Umbrel: WordPress with the [MainWP](https://mainwp.com) Dashboard preinstalled, self-installing like WP Sandbox.

Add each site by address and admin login — MainWP installs its companion plugin and then gives you one-click sign-in, plugin/theme management, and bulk updates across the whole fleet, all from one dashboard.

### Preview Server

A tiny nginx server for previewing static sites — HTML mockups, prototypes and client drafts — straight from your Umbrel.

Drop files into its data folder (directory listing included), or set `PREVIEW_FETCH_URL` to a `.tar.gz` and it pulls and unpacks the site on every start. No login required.

## Install

1. Open **App Store** on your Umbrel
2. Open the **Community App Stores** menu (top right)
3. Paste: `https://github.com/DeusMozart/umbrel-app-store`
4. Find the app you want in the store and install it
