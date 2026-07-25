import path from "node:path";
import { defineConfig } from "vite";
import solidPlugin from "vite-plugin-solid";

const frontendPort = Number(process.env.OPENCREW_FRONTEND_PORT || "18080");
const frontendHost = process.env.OPENCREW_FRONTEND_HOST || "127.0.0.1";
const backendTarget = process.env.OPENCREW_BACKEND_URL || "http://127.0.0.1:8011";
const allowedHosts = (process.env.OPENCREW_ALLOWED_HOSTS || "localhost,127.0.0.1,www.goldenstand.cn,goldenstand.cn,opencrew.goldenstand.cn,opencrew.instmarket.com.au,.trycloudflare.com")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const noStoreHeaders = {
  "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0",
  "Pragma": "no-cache",
  "Expires": "0",
  "Surrogate-Control": "no-store",
};
const immutableAssetHeaders = {
  "Cache-Control": "public, max-age=31536000, immutable",
};
const previewImmutableAssetPath = (pathname: string) =>
  pathname.startsWith("/assets/")
  || pathname.startsWith("/hyperframe_templates/previews/")
  || pathname === "/favicon.svg";
const previewNoStorePath = (pathname: string) => pathname === "/" || pathname === "/index.html";
type HeaderResponse = {
  setHeader: (name: string, value: string) => void;
  removeHeader?: (name: string) => void;
  writeHead: (...args: unknown[]) => unknown;
};
const setHeaders = (response: { setHeader: (name: string, value: string) => void }, headers: Record<string, string>) => {
  for (const [name, value] of Object.entries(headers)) response.setHeader(name, value);
};
const forceHeadersOnWrite = (response: HeaderResponse, headers: Record<string, string>, removeHeaders: string[] = []) => {
  const originalWriteHead = response.writeHead.bind(response);
  response.writeHead = (...args: unknown[]) => {
    for (const name of removeHeaders) response.removeHeader?.(name);
    setHeaders(response, headers);
    return originalWriteHead(...args);
  };
};
const opencrewPreviewCacheHeaders = () => ({
  name: "opencrew-preview-cache-headers",
  configurePreviewServer(server: { middlewares: { use: (handler: (request: { url?: string }, response: HeaderResponse, next: () => void) => void) => void } }) {
    server.middlewares.use((request, response, next) => {
      const pathname = new URL(request.url || "/", "http://opencrew.local").pathname;
      if (previewImmutableAssetPath(pathname)) {
        forceHeadersOnWrite(response, immutableAssetHeaders, ["Pragma", "Expires", "Surrogate-Control"]);
      } else if (previewNoStorePath(pathname)) {
        forceHeadersOnWrite(response, noStoreHeaders);
      }
      next();
    });
  },
});
const apiProxy = {
  "/api": {
    target: backendTarget,
    changeOrigin: true,
  },
  "/session/share": {
    target: backendTarget,
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [opencrewPreviewCacheHeaders(), solidPlugin()],
  resolve: {
    extensions: [".tsx", ".ts", ".jsx", ".js", ".mjs", ".mts", ".json"],
    alias: {
      "solid-js": path.resolve(__dirname, "node_modules/solid-js"),
    },
  },
  server: {
    host: frontendHost,
    port: frontendPort,
    hmr: false,
    allowedHosts,
    headers: noStoreHeaders,
    fs: {
      allow: [path.resolve(__dirname, "..")],
    },
    proxy: apiProxy,
  },
  preview: {
    host: frontendHost,
    port: frontendPort,
    allowedHosts,
    proxy: apiProxy,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/node_modules/solid-js/")) return "solid";
          if (id.includes("/node_modules/")) return "vendor";
          if (id.includes("/ModelConfig/")) return "model-config";
        },
      },
    },
  },
});
