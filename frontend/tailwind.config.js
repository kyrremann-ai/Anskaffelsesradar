/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)", surface: "var(--surface)", sunk: "var(--sunk)", fg: "var(--fg)", muted: "var(--muted)",
        line: "var(--line)", accent: "var(--accent)", "accent-soft": "var(--accent-soft)", ink: "var(--ink)",
        "ink-fg": "var(--ink-fg)", hi: "var(--hi)", "hi-soft": "var(--hi-soft)", mid: "var(--mid)",
        "mid-soft": "var(--mid-soft)", lo: "var(--lo)", "lo-soft": "var(--lo-soft)",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
