import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  plugins: [react()],
  css: {
    postcss: {
      plugins: [{
        postcssPlugin: "scope-kanban-styles",
        Once(root) {
          root.walkRules(rule => {
            if (rule.parent?.type === "atrule" && /keyframes$/.test(rule.parent.name)) return;
            rule.selectors = rule.selectors.map(selector => {
              const scoped = selector.replace(/:root\b|(?<![\w-])body(?![\w-])/g, "#root");
              return scoped.startsWith("#root") ? scoped : `#root ${scoped}`;
            });
          });
        },
      }],
    },
  },
  build: {
    outDir: "dist/kanban",
    lib: { entry: "src/main.tsx", formats: ["es"], fileName: () => "kanban.js", cssFileName: "kanban" },
  },
});
