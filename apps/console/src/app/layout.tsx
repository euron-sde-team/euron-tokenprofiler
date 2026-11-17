import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/components/providers";
import { THEME_INIT_SCRIPT } from "@/hooks/use-theme";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Euron TokenProfiler", template: "%s · Euron TokenProfiler" },
  description: "Track, analyze and cut LLM token usage and cost across models, prompts and applications.",
  icons: { icon: "/icon.svg" },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
