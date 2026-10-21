import { useEffect } from "react";
import { useBlocker } from "react-router-dom";
import { Dialog } from "./ui";

/**
 * Ask before leaving a page that holds work the user has not saved (a half-filled form, an upload in progress).
 * Covers in-app links, the browser's Back and Forward, and closing or reloading the tab. Render the returned element.
 */
export function useUnsavedChanges(dirty: boolean) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname + currentLocation.search !== nextLocation.pathname + nextLocation.search);
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const stay = () => blocker.reset?.();
  if (blocker.state !== "blocked") return null;
  return (
    <Dialog title="Leave without saving?" onClose={stay}>
      <p className="muted">What you entered on this page has not been saved. If you leave now, it will be lost.</p>
      <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
        <button type="button" className="btn primary" onClick={stay}>Stay</button>
        <button type="button" className="btn" onClick={() => blocker.proceed()}>Leave</button>
      </div>
    </Dialog>
  );
}
