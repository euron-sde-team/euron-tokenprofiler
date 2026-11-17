import { Suspense, type ReactNode } from "react";
import { Shell } from "@/components/shell";

// Pages read filters from the URL (useSearchParams), which needs a Suspense boundary during static prerendering.
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <Shell>
      <Suspense fallback={null}>{children}</Suspense>
    </Shell>
  );
}
