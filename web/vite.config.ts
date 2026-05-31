import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: "./" so the build works both on a Netlify root and from a local preview.
export default defineConfig({
  plugins: [react()],
  base: "./",
});
