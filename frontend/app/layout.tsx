import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RevWin Market Analysis",
  description: "AEC firm market analysis report generator",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
