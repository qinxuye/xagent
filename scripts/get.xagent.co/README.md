# get.xagent.co

Cloudflare Worker that serves [`scripts/install.sh`](../install.sh) so users can install Xagent with:

```bash
curl -fsSL https://get.xagent.co | sh
```

The Worker fetches the installer pinned to the **latest GitHub release tag** (falling back to `main` only if the release lookup fails), so the public one-liner always serves a shipped, reviewed version.

## Deploy

Requires [`wrangler`](https://developers.cloudflare.com/workers/wrangler/) and access to the Cloudflare account that owns the `xagent.co` zone.

```bash
cd scripts/get.xagent.co
wrangler deploy
```

Then map `get.xagent.co` to this Worker (the `routes` entry in `wrangler.toml` does this once the zone is on the account).

## Notes

- The Worker only serves the script; it runs no user code.
- Edge-caches the resolved script for 5 minutes (`CACHE_TTL_SECONDS`).
- To publish a change to the installer: merge it to `main`, then it goes live at the next release (or immediately via the `main` fallback if there is no release yet).
