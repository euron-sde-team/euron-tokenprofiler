import { Compass } from "lucide-react";
import Link from "next/link";
import { EmptyState } from "@/components/ui/primitives";

export default function NotFound() {
  return (
    <div className="card mt-10">
      <EmptyState
        icon={<Compass className="size-5" />}
        title="Page not found"
        hint="That page does not exist. Use the navigation, or go back to the overview."
        action={<Link href="/" className="rounded-lg border border-line bg-raised px-3.5 py-2 text-[13px] font-medium hover:border-subtle/60">Back to the overview</Link>}
      />
    </div>
  );
}
