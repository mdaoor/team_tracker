import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { registerServiceWorker } from "./register";

/** The service worker is plain JS in `public/` (it is served as-is, not bundled), so the tests evaluate the real file against a fake
 * `caches` and `fetch`. The real-browser proof (install, control, offline reload) is `e2e/mobile.spec.ts`. */
const SRC = readFileSync(resolve(__dirname, "../../public/sw.js"), "utf8");
const SCOPE = "https://tt.test/m/";
const PAGE = `${SCOPE}mobile.html`;
const HTML = `<!doctype html><link rel="manifest" href="/m/manifest.webmanifest"><link rel="stylesheet" href="/m/assets/mobile-abc.css">
<script type="module" src="/m/assets/mobile-abc.js"></script><script src="https://cdn.example/x.js"></script><a href="/m/">root</a>`;

type Req = { method: string; url: string; mode?: string };
function world(network: Record<string, string>) {
  const stores = new Map<string, Map<string, Response>>();
  const fetched: string[] = [];
  let offline = false;
  const keyOf = (r: string | Req) => (typeof r === "string" ? r : r.url);
  const caches = {
    open: async (name: string) => {
      if (!stores.has(name)) stores.set(name, new Map());
      const m = stores.get(name)!;
      return {
        put: async (r: string | Req, res: Response) =>
          void m.set(keyOf(r), res),
        keys: async () => [...m.keys()].map((url) => ({ url })),
        addAll: async () => undefined,
      };
    },
    match: async (r: string | Req) => {
      for (const m of stores.values()) {
        const hit = m.get(keyOf(r));
        if (hit) return hit.clone();
      }
      return undefined;
    },
    keys: async () => [...stores.keys()],
    delete: async (n: string) => stores.delete(n),
  };
  const doFetch = vi.fn(async (r: string | Req) => {
    const url = keyOf(r);
    fetched.push(url);
    if (offline) throw new TypeError("Failed to fetch");
    if (!(url in network)) return new Response("nope", { status: 404 });
    return new Response(network[url], { status: 200 });
  });
  const listeners: Record<string, (e: unknown) => void> = {};
  const self = {
    registration: { scope: SCOPE },
    addEventListener: (t: string, f: (e: unknown) => void) =>
      (listeners[t] = f),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn(async () => undefined) },
  };
  new Function("self", "caches", "fetch", "URL", SRC)(
    self,
    caches,
    doFetch,
    URL,
  );
  const wait = async (type: string) => {
    let p: Promise<unknown> = Promise.resolve();
    listeners[type]({ waitUntil: (x: Promise<unknown>) => (p = x) });
    await p;
  };
  const request = async (req: Req) => {
    let responded: Promise<Response> | undefined;
    listeners.fetch({
      request: req,
      respondWith: (p: Promise<Response>) => (responded = p),
    });
    return responded ? await responded : undefined;
  };
  return {
    self,
    stores,
    fetched,
    wait,
    request,
    setOffline: (v: boolean) => (offline = v),
    cachedUrls: () => [...(stores.get("tt-mobile-v1")?.keys() ?? [])].sort(),
  };
}
const NET = {
  [PAGE]: HTML,
  [`${SCOPE}manifest.webmanifest`]: "{}",
  [`${SCOPE}assets/mobile-abc.css`]: "body{}",
  [`${SCOPE}assets/mobile-abc.js`]: "console.log(1)",
  [`${SCOPE}icons/icon-192.png`]: "png",
  [`${SCOPE}icons/icon-512.png`]: "png",
};

describe("service worker", () => {
  it("install caches the shell page and every same-origin asset it names, hashed or not, plus the icons", async () => {
    const w = world(NET);
    await w.wait("install");
    expect(w.cachedUrls()).toEqual(
      [
        PAGE,
        `${SCOPE}assets/mobile-abc.css`,
        `${SCOPE}assets/mobile-abc.js`,
        `${SCOPE}icons/icon-192.png`,
        `${SCOPE}icons/icon-512.png`,
        `${SCOPE}manifest.webmanifest`,
      ].sort(),
    );
    expect(w.fetched).not.toContain("https://cdn.example/x.js"); // other origins are never fetched or cached
    expect(w.self.skipWaiting).toHaveBeenCalled();
  });

  it("one asset failing does not fail the install; the shell page failing does", async () => {
    const w = world(
      Object.fromEntries(
        Object.entries(NET).filter(([k]) => !k.endsWith(".css")),
      ),
    );
    await w.wait("install");
    expect(w.cachedUrls()).toContain(PAGE);
    expect(w.cachedUrls().some((u) => u.endsWith(".css"))).toBe(false);
    expect(w.cachedUrls().some((u) => u.endsWith(".js"))).toBe(true);
    const none = world({});
    await expect(none.wait("install")).rejects.toThrow(
      /shell page not available/,
    );
  });

  it("opens the cached shell for a navigation when the network is gone, and refreshes it when online", async () => {
    const net = { ...NET };
    const w = world(net);
    await w.wait("install");
    w.setOffline(true);
    const res = await w.request({ method: "GET", url: PAGE, mode: "navigate" });
    expect(await res!.text()).toContain("mobile-abc.js");
    const deep = await w.request({
      method: "GET",
      url: `${SCOPE}mobile.html?x=1`,
      mode: "navigate",
    });
    expect(deep!.status).toBe(200); // any navigation in scope falls back to the shell
    w.setOffline(false);
    net[PAGE] = HTML.replace("abc", "def");
    const fresh = await w.request({
      method: "GET",
      url: PAGE,
      mode: "navigate",
    });
    expect(await fresh!.text()).toContain("def");
  });

  it("serves assets from the cache first and fills the cache on first use", async () => {
    const w = world({ ...NET, [`${SCOPE}assets/late.js`]: "late" });
    await w.wait("install");
    const before = w.fetched.length;
    await w.request({ method: "GET", url: `${SCOPE}assets/mobile-abc.js` });
    expect(w.fetched.length).toBe(before); // from the cache, no network
    await w.request({ method: "GET", url: `${SCOPE}assets/late.js` });
    expect(w.fetched.length).toBe(before + 1);
    w.setOffline(true);
    expect(
      await (await w.request({
        method: "GET",
        url: `${SCOPE}assets/late.js`,
      }))!.text(),
    ).toBe("late");
  });

  it("never touches the API, other origins or anything but GET", async () => {
    const w = world(NET);
    await w.wait("install");
    expect(
      await w.request({
        method: "GET",
        url: "https://tt.test/api/v1/w/acme/capture/batches",
      }),
    ).toBeUndefined();
    expect(
      await w.request({ method: "POST", url: `${SCOPE}assets/mobile-abc.js` }),
    ).toBeUndefined();
    expect(
      await w.request({
        method: "GET",
        url: "https://other.test/m/mobile.html",
        mode: "navigate",
      }),
    ).toBeUndefined();
    expect(w.cachedUrls().some((u) => u.includes("/api/"))).toBe(false);
  });

  it("activate removes old caches of this app only and takes control", async () => {
    const w = world(NET);
    await w.wait("install");
    w.stores.set("tt-mobile-v0", new Map());
    w.stores.set("someone-elses", new Map());
    await w.wait("activate");
    expect([...w.stores.keys()].sort()).toEqual([
      "someone-elses",
      "tt-mobile-v1",
    ]);
    expect(w.self.clients.claim).toHaveBeenCalled();
  });
});

describe("registration", () => {
  it("registers ./sw.js in production only, and swallows failures", async () => {
    const register = vi.fn(async () => ({}) as ServiceWorkerRegistration);
    const nav = { serviceWorker: { register } } as unknown as Navigator;
    expect(await registerServiceWorker(nav, false)).toBe(false);
    expect(register).not.toHaveBeenCalled();
    expect(await registerServiceWorker(nav, true)).toBe(true);
    expect(register).toHaveBeenCalledWith("./sw.js");
    const bad = {
      serviceWorker: {
        register: async () => Promise.reject(new Error("blocked")),
      },
    } as unknown as Navigator;
    expect(await registerServiceWorker(bad, true)).toBe(false);
    expect(await registerServiceWorker({} as Navigator, true)).toBe(false);
  });
});

describe("manifest", () => {
  const m = JSON.parse(
    readFileSync(
      resolve(__dirname, "../../public/manifest.webmanifest"),
      "utf8",
    ),
  );
  it("is installable: name, standalone, start_url inside scope, 192 and 512 PNGs that exist, a maskable icon", () => {
    expect(m.name && m.short_name).toBeTruthy();
    expect(m.display).toBe("standalone");
    expect(m.start_url).toBe("./mobile.html");
    expect(m.scope).toBe("./");
    const sizes = m.icons.map((i: { sizes: string }) => i.sizes);
    expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    expect(
      m.icons.some((i: { purpose: string }) => i.purpose === "maskable"),
    ).toBe(true);
    for (const i of m.icons) {
      const png = readFileSync(resolve(__dirname, "../../public", i.src));
      expect(png.subarray(1, 4).toString()).toBe("PNG");
      const [w, h] = [png.readUInt32BE(16), png.readUInt32BE(20)];
      expect(`${w}x${h}`).toBe(i.sizes);
    }
  });
  it("is linked from mobile.html with a phone viewport and a theme colour", () => {
    const html = readFileSync(resolve(__dirname, "../../mobile.html"), "utf8");
    expect(html).toContain('rel="manifest" href="/manifest.webmanifest"');
    expect(html).toContain("width=device-width");
    expect(html).toContain('name="theme-color"');
    expect(html).toContain("/src/mobile/main.tsx");
  });
});
