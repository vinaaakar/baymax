import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import Companion from "./views/Companion";
import Conversation from "./views/Conversation";
import Workspace from "./views/Workspace";

function route(): "companion" | "conversation" | "workspace" {
  const h = location.hash.replace(/^#\/?/, "").split("?")[0];
  return h === "conversation" || h === "workspace" ? h : "companion";
}

function App() {
  const [r, setR] = useState(route());
  useEffect(() => {
    const on = () => setR(route());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  document.title = r === "workspace" ? "BAYMAX — Workspace" : r === "conversation" ? "BAYMAX — Conversation" : "BAYMAX";
  return r === "workspace" ? <Workspace /> : r === "conversation" ? <Conversation /> : <Companion />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
