import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import legacy from "@vitejs/plugin-legacy";

// https://vitejs.dev/config/
export default defineConfig({
  cacheDir: '.vite-cache',
  plugins: [
    react({
      babel: {
        plugins: [
          "@babel/plugin-proposal-optional-chaining", // Transpile for older browsers.
        ],
      },
    }),
    legacy({
      targets: ["defaults", "ie >= 11", "chrome >= 52"], // Supported browser targets.
      additionalLegacyPolyfills: ["regenerator-runtime/runtime"],
      renderLegacyChunks: true,
      polyfills: [
        "es.symbol",
        "es.array.filter",
        "es.promise",
        "es.promise.finally",
        "es/map",
        "es/set",
        "es.array.for-each",
        "es.object.define-properties",
        "es.object.define-property",
        "es.object.get-own-property-descriptor",
        "es.object.get-own-property-descriptors",
        "es.object.keys",
        "es.object.to-string",
        "web.dom-collections.for-each",
        "esnext.global-this",
        "esnext.string.match-all",
      ],
    }),
  ],
  build: {
    target: "es5",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  css: {
    modules: {
      localsConvention: "camelCase",
    },
  },
  server: {
    port: 8080,
    proxy: {
      "/solve": {
        target: process.env.VERITRACE_API_URL || "http://127.0.0.1:8002",
        changeOrigin: true,
      },
      "/api": {
        target: process.env.VERITRACE_API_URL || "http://127.0.0.1:8002",
        changeOrigin: true,
      },
    },
  },
});
