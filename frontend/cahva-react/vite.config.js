// vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0', // This tells Vite to listen on all network interfaces
    port: 5173,      // Ensure this matches the port you are using
  }
})