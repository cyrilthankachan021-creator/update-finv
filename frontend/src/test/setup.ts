import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
window.scrollTo = vi.fn();
Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });   // laptop width: the assistant opens beside the page
