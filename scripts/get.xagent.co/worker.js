// Cloudflare Worker backing https://get.xagent.co
//
// Serves scripts/install.sh from the xagent repo so users can run:
//
//   curl -fsSL https://get.xagent.co | sh
//
// The script is pinned to the latest GitHub *release tag* (not `main`), so the
// public one-liner always fetches a shipped, reviewed version. Falls back to
// `main` only if the release lookup fails. Deploy with `wrangler deploy`.

const REPO = "xorbitsai/xagent";
const SCRIPT_PATH = "scripts/install.sh";
const FALLBACK_REF = "main";
// Cache the resolved script at the edge to avoid hitting GitHub on every hit.
const CACHE_TTL_SECONDS = 300;

async function latestReleaseTag() {
  const res = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
    headers: { "User-Agent": "get.xagent.co", Accept: "application/vnd.github+json" },
    cf: { cacheTtl: CACHE_TTL_SECONDS, cacheEverything: true },
  });
  if (!res.ok) return null;
  const data = await res.json();
  return typeof data.tag_name === "string" && data.tag_name ? data.tag_name : null;
}

async function fetchScript(ref) {
  const url = `https://raw.githubusercontent.com/${REPO}/${ref}/${SCRIPT_PATH}`;
  return fetch(url, {
    headers: { "User-Agent": "get.xagent.co" },
    cf: { cacheTtl: CACHE_TTL_SECONDS, cacheEverything: true },
  });
}

export default {
  async fetch() {
    const ref = (await latestReleaseTag()) || FALLBACK_REF;

    let res = await fetchScript(ref);
    if (!res.ok && ref !== FALLBACK_REF) {
      res = await fetchScript(FALLBACK_REF); // tag exists but file missing at that tag
    }
    if (!res.ok) {
      return new Response("# Xagent installer temporarily unavailable\n", {
        status: 502,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    const body = await res.text();
    return new Response(body, {
      status: 200,
      headers: {
        // text/plain so `curl | sh` gets the raw script, never rendered HTML.
        "content-type": "text/plain; charset=utf-8",
        "cache-control": `public, max-age=${CACHE_TTL_SECONDS}`,
        "x-xagent-install-ref": ref,
      },
    });
  },
};
