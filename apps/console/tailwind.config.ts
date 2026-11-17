import type { Config } from "tailwindcss";

// Colours come from CSS variables (see globals.css) so the dark and light themes share one set of classes.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: v("bg"),
        surface: v("surface"),
        raised: v("raised"),
        line: v("line"),
        fg: v("fg"),
        muted: v("muted"),
        subtle: v("subtle"),
        accent: v("accent"),
        "accent-fg": v("accent-fg"),
        waiting: v("waiting"),
        delayed: v("delayed"),
        active: v("active"),
        completed: v("completed"),
        failed: v("failed"),
        cancelled: v("cancelled"),
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: { pop: "0 12px 40px -8px rgb(0 0 0 / 0.45)" },
      keyframes: {
        pulseDot: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
        slideIn: { from: { transform: "translateX(24px)", opacity: "0" }, to: { transform: "none", opacity: "1" } },
        fadeIn: { from: { opacity: "0" }, to: { opacity: "1" } },
      },
      animation: {
        pulseDot: "pulseDot 1.6s ease-in-out infinite",
        slideIn: "slideIn 180ms ease-out",
        fadeIn: "fadeIn 150ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
