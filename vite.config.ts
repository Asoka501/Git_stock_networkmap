import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

// base './' keeps asset paths relative so the build works
// under a GitHub Pages project subpath (/<repo>/).
export default defineConfig({
  base: './',
  plugins: [tailwindcss()],
})
