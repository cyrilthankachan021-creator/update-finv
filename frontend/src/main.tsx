import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { App } from "./App";
import { installStartGuard } from "./lib/navigation";
import { SessionProvider } from "./session";
import "./styles/app.css";

installStartGuard();   // before the router reads the history: Back stops at the first page instead of leaving FinV

// A data router (rather than <BrowserRouter>) is what lets pages ask "Leave without saving?" before Back or a link leaves.
const router = createBrowserRouter([{ path: "*", element: <SessionProvider><App /></SessionProvider> }], {
  future: { v7_relativeSplatPath: true, v7_fetcherPersist: true, v7_normalizeFormMethod: true, v7_partialHydration: true, v7_skipActionErrorRevalidation: true },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><RouterProvider router={router} future={{ v7_startTransition: true }} /></React.StrictMode>,
);
