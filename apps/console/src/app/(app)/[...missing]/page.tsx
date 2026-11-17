import { notFound } from "next/navigation";

// Any unknown path under the app shell renders the app's own 404 (see ../not-found.tsx), with navigation intact.
export default function Missing() {
  notFound();
}
