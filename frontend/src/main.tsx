import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router";
import "./index.css";
import App from "./App";
import Dashboard from "./pages/Dashboard";
import Library from "./pages/Library";
import Import from "./pages/Import";
import ReviewQueue from "./pages/ReviewQueue";
import Viewer from "./pages/Viewer";
import Stats from "./pages/Stats";
import Settings from "./pages/Settings";
import Editor from "./pages/Editor";
import People from "./pages/People";
import Guide from "./pages/Guide";
import PoseSetup from "./pages/PoseSetup";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "library", element: <Library /> },
      { path: "import", element: <Import /> },
      { path: "review", element: <ReviewQueue /> },
      { path: "people", element: <People /> },
      { path: "guide", element: <Guide /> },
      { path: "setup", element: <PoseSetup /> },
      { path: "viewer", element: <Viewer /> },
      { path: "editor/:photoId", element: <Editor /> },
      { path: "stats", element: <Stats /> },
      { path: "settings", element: <Settings /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
