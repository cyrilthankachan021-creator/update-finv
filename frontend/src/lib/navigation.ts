/** Navigation rules shared by the whole app: where "back" stops, and how list/step positions are clamped. */
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

export const AT_START_EVENT = "finv:at-start";

/**
 * Makes the first page of a visit the bottom of the stack. Opened fresh (no router state yet), the current entry is
 * replaced by a marker and the same URL is pushed above it. If Back ever lands on the marker we step forward again and
 * announce "You're at the start": Back never leaves FinV, and there is no loop, because the marker is only ever entered
 * by a person pressing Back. Call once, before the router is created.
 */
export function installStartGuard(w: Window = window): () => void {
  if (w.history.state == null) {
    w.history.replaceState({ finvStart: true }, "");
    w.history.pushState(null, "");
  }
  const onPop = (e: PopStateEvent) => {
    if (e.state && (e.state as { finvStart?: boolean }).finvStart) {
      w.history.go(1);
      w.dispatchEvent(new Event(AT_START_EVENT));
    }
  };
  w.addEventListener("popstate", onPop);
  return () => w.removeEventListener("popstate", onPop);
}

/** Position moves that stop at both ends instead of wrapping (steppers, tab lists, arrow-key navigation). */
export const clampIndex = (i: number, total: number): number => (total <= 0 ? 0 : Math.min(Math.max(i, 0), total - 1));

export function useStepper(total: number, start = 0) {
  const [index, setIndex] = useState(() => clampIndex(start, total));
  return {
    index, total, isFirst: index <= 0, isLast: index >= total - 1,
    next: () => setIndex((i) => clampIndex(i + 1, total)),
    back: () => setIndex((i) => clampIndex(i - 1, total)),
    go: (i: number) => setIndex(clampIndex(i, total)),
  };
}

/** Keyboard handler for a list or tab strip: arrows move one item and stop at the first and last. */
export function arrowNav(index: number, total: number, set: (i: number) => void, vertical = false) {
  const [prev, next] = vertical ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
  return (e: { key: string; preventDefault: () => void }) => {
    const to = e.key === prev ? index - 1 : e.key === next ? index + 1 : e.key === "Home" ? 0 : e.key === "End" ? total - 1 : null;
    if (to === null) return;
    e.preventDefault();
    set(clampIndex(to, total));
  };
}

const SCROLL_KEY = "finv.scroll";
const readScroll = (): Record<string, number> => { try { return JSON.parse(sessionStorage.getItem(SCROLL_KEY) || "{}"); } catch { return {}; } };

/** Scroll to `y` once the page is tall enough (lists load after the route changes), giving up after about a second. */
export function restoreScroll(y: number, w: Window = window, frames = 60): () => void {
  let n = 0, raf = 0;
  const step = () => {
    const tall = w.document.documentElement.scrollHeight >= y + w.innerHeight;
    if (tall || n++ >= frames) { w.scrollTo(0, y); return; }
    raf = w.requestAnimationFrame(step);
  };
  step();
  return () => w.cancelAnimationFrame(raf);
}

/** Back/Forward return to where the list was scrolled; a new page starts at the top; filter changes (replace) stay put. */
export function useScrollRestoration() {
  const loc = useLocation();
  const type = useNavigationType();
  const positions = useRef<Record<string, number>>(readScroll());
  const key = loc.key;
  useEffect(() => {
    // The page has already changed by the time this cleanup runs, so window.scrollY is no longer the old page's: keep the last seen value.
    let latest = positions.current[key] ?? window.scrollY;
    const save = () => { positions.current[key] = latest; try { sessionStorage.setItem(SCROLL_KEY, JSON.stringify(positions.current)); } catch { /* storage unavailable */ } };
    let t = 0;
    const onScroll = () => { latest = window.scrollY; window.clearTimeout(t); t = window.setTimeout(save, 100); };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.removeEventListener("scroll", onScroll); window.clearTimeout(t); save(); };
  }, [key]);
  useEffect(() => {
    if (type === "POP") return restoreScroll(positions.current[key] ?? 0);
    if (type === "PUSH") window.scrollTo(0, 0);
    return undefined;
  }, [key, type]);
}
