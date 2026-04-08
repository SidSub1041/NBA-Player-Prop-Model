import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NBA Player Prop Model",
  description:
    "Data-driven NBA player prop picks using DvP analysis, shooting zones, playtypes, and hit rates.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
