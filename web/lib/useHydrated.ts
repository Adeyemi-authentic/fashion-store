import { useSyncExternalStore } from "react";

const noop = () => () => {};

/** false in the server HTML and until React has hydrated in the browser, then true. Buttons
 * that only work once hydrated use it to stay disabled until then, so an early tap on a fast
 * prerendered page isn't silently lost. */
export function useHydrated(): boolean {
  return useSyncExternalStore(noop, () => true, () => false);
}
