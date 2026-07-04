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
  // Never throw: a GitHub API outage/rate-limit must degrade to the main
  // fallback, not crash the installer endpoint.
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
      headers: { "User-Agent": "get.xagent.co", Accept: "application/vnd.github+json" },
      cf: { cacheTtl: CACHE_TTL_SECONDS, cacheEverything: true },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return typeof data.tag_name === "string" && data.tag_name ? data.tag_name : null;
  } catch {
    return null;
  }
}

async function fetchScript(ref) {
  const url = `https://raw.githubusercontent.com/${REPO}/${ref}/${SCRIPT_PATH}`;
  return fetch(url, {
    headers: { "User-Agent": "get.xagent.co" },
    cf: { cacheTtl: CACHE_TTL_SECONDS, cacheEverything: true },
  });
}

const UNAVAILABLE = () =>
  new Response("# Xagent installer temporarily unavailable\n", {
    status: 502,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });

export default {
  async fetch(request) {
    // Only the root path serves the installer; ignore /favicon.ico etc.
    if (new URL(request.url).pathname !== "/") {
      return new Response("Not Found\n", {
        status: 404,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    try {
      const ref = (await latestReleaseTag()) || FALLBACK_REF;

      let servedRef = ref;
      let res = await fetchScript(ref);
      if (!res.ok && ref !== FALLBACK_REF) {
        res = await fetchScript(FALLBACK_REF); // tag exists but file missing at that tag
        servedRef = FALLBACK_REF;
      }
      if (!res.ok) return UNAVAILABLE();

      const body = await res.text();
      return new Response(body, {
        status: 200,
        headers: {
          // text/plain so `curl | sh` gets the raw script, never rendered HTML.
          "content-type": "text/plain; charset=utf-8",
          "cache-control": `public, max-age=${CACHE_TTL_SECONDS}`,
          "x-xagent-install-ref": servedRef,
        },
      });
    } catch {
      // Any unexpected error → clean 502 text, never a Cloudflare HTML 500
      // (which would break a piped `curl | sh`).
      return UNAVAILABLE();
    }
  },
};
