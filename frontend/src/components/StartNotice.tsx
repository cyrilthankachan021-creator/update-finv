import { useEffect, useState } from "react";
import { AT_START_EVENT } from "../lib/navigation";

/** Brief message shown when Back reaches the first page of the visit and stays there. */
export function StartNotice() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    let t = 0;
    const on = () => { setShow(true); window.clearTimeout(t); t = window.setTimeout(() => setShow(false), 2500); };
    window.addEventListener(AT_START_EVENT, on);
    return () => { window.removeEventListener(AT_START_EVENT, on); window.clearTimeout(t); };
  }, []);
  return <div role="status" className={`start-notice${show ? " show" : ""}`}>{show ? "You're at the start" : ""}</div>;
}
