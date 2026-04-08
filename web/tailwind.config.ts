import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        court: {
          50: "#f0f4ff",
          100: "#dbe3ff",
          200: "#bfcbff",
          300: "#93a6ff",
          400: "#6070ff",
          500: "#3b42ff",
          600: "#2a1ff7",
          700: "#2418d9",
          800: "#1e16af",
          900: "#1d178a",
          950: "#111036",
        },
      },
    },
  },
  plugins: [],
};

export default config;
