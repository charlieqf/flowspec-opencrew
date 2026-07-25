import { Show, createEffect, onCleanup } from "solid-js";
import { CloseIcon, DeleteIcon } from "../components/MediaLibraryIcons.jsx";

export default function MediaLibraryDeleteDialog(props) {
  const asset = () => props.asset;
  const sessionText = () => asset()?.sessionId ? `Session #${asset().sessionId}` : "对应 Session";
  const blocked = () => Number(asset()?.referencedByCount || 0) > 0;
  const close = () => {
    if (!props.busy) props.onClose?.();
  };

  createEffect(() => {
    if (!asset()) return;
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event) => {
      if (event.key === "Escape") close();
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);
    onCleanup(() => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    });
  });

  return <Show when={asset()}>
    <div class="media-library-delete-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <section class="media-library-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="media-library-delete-title" aria-describedby="media-library-delete-description">
        <header>
          <div class="media-library-delete-heading"><span><DeleteIcon /></span><div><h3 id="media-library-delete-title">永久删除素材</h3><p id="media-library-delete-description">此操作会删除素材对应的完整 Session，无法恢复。</p></div></div>
          <button type="button" class="media-library-delete-close" title="关闭" aria-label="关闭删除确认" disabled={props.busy} onClick={close}><CloseIcon /></button>
        </header>

        <div class="media-library-delete-body">
          <div class="media-library-delete-target"><span>即将删除</span><strong title={asset().displayName}>{asset().displayName}</strong><small>{sessionText()}</small></div>
          <div class="media-library-delete-scope">
            <h4>将同时永久删除</h4>
            <ul>
              <li><span>1</span><div><strong>素材库记录与分析任务</strong><small>素材索引、分析状态与任务记录</small></div></li>
              <li><span>2</span><div><strong>{sessionText()} 及全部 Session 记录</strong><small>Session Context、Input、Output 与运行记录</small></div></li>
              <li><span>3</span><div><strong>Session 文件夹中的全部文件</strong><small>源视频、关键帧、字幕识别和分析结果</small></div></li>
            </ul>
          </div>
          <Show when={blocked()} fallback={<div class="media-library-delete-warning">删除后无法恢复，请确认不再需要该素材及其分析结果。</div>}>
            <div class="media-library-delete-blocked">该素材已被 {asset().referencedByCount} 个任务或工程引用。请先解除引用，再执行删除。</div>
          </Show>
          <Show when={props.error}><div class="media-library-delete-error" role="alert">{props.error}</div></Show>
        </div>

        <footer>
          <button type="button" class="secondary" disabled={props.busy} onClick={close}>取消</button>
          <button type="button" class="danger" disabled={props.busy || blocked()} onClick={() => props.onConfirm?.(asset())}><DeleteIcon />{props.busy ? "正在删除…" : blocked() ? "暂不能删除" : "永久删除"}</button>
        </footer>
      </section>
    </div>
  </Show>;
}
