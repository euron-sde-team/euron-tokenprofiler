/** Three rising bars: tokens counted, spend tracked. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="rgb(var(--accent))" />
      <rect x="7" y="17" width="4" height="8" rx="1" fill="rgb(var(--accent-fg))" />
      <rect x="14" y="11" width="4" height="14" rx="1" fill="rgb(var(--accent-fg))" />
      <rect x="21" y="6" width="4" height="19" rx="1" fill="rgb(var(--accent-fg))" />
    </svg>
  );
}
