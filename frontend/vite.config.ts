import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  plugins: [react()],
  build: {
    outDir: "dist/kanban",
    lib: { entry: "src/main.tsx", formats: ["es"], fileName: () => "kanban.js", cssFileName: "kanban" },
  },
});
