export default function MediaLibraryAssetActions(props) {
  return (
    <div class="media-library-action-menu" onClick={(event) => event.stopPropagation()}>
      <button type="button" onClick={() => props.onRename(props.asset)}>重命名</button>
      <button type="button" onClick={() => props.onEditTags(props.asset)}>编辑标签</button>
      <button type="button" onClick={() => props.asset.archived ? props.onRestore(props.asset) : props.onArchive(props.asset)}>{props.asset.archived ? "恢复归档" : "归档"}</button>
    </div>
  );
}
