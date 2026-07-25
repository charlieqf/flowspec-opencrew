import { Show, createMemo } from "solid-js";
import MediaLibraryDetailPage from "./pages/MediaLibraryDetailPage.jsx";
import MediaLibraryEditorPage from "./pages/MediaLibraryEditorPage.jsx";
import MediaLibraryListPage from "./pages/MediaLibraryListPage.jsx";
import { mediaLibraryRouteFromHash } from "./mediaLibraryModel.js";
import "./mediaLibrary.css";

export default function MediaLibraryModule(props) {
  const route = createMemo(() => mediaLibraryRouteFromHash(props.routeHash));
  return <Show when={route().view === "editor"} fallback={
    <Show when={route().view === "detail"} fallback={
      <>
        <Show when={route().view === "invalid" && route().error}>
          <div class="media-library-banner bad"><span>{route().error}</span></div>
        </Show>
        <MediaLibraryListPage routeHash={props.routeHash} />
      </>
    }>
      <MediaLibraryDetailPage assetId={route().assetId} />
    </Show>
  }>
    <MediaLibraryEditorPage route={route()} />
  </Show>;
}
