import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import {
  mediaLibraryActiveFilterCount,
  resetMediaLibraryFilters,
} from "../mediaLibraryFilterModel.js";

function FilterSelect(props) {
  return (
    <label class={props.wide ? "is-wide" : ""}>
      <span>{props.label}</span>
      <select ref={props.inputRef} value={props.value} onChange={(event) => props.onChange(event.currentTarget.value)}>
        {props.children}
      </select>
    </label>
  );
}

function MediaLibraryFilterDialog(props) {
  const [draft, setDraft] = createSignal({ ...props.filters });
  const selectedCount = createMemo(() => mediaLibraryActiveFilterCount(draft()));
  let firstSelect;

  createEffect(() => {
    if (!props.open) return;
    setDraft({ ...props.filters });
    const previousOverflow = document.body.style.overflow;
    const onKeyDown = (event) => {
      if (event.key === "Escape") props.onClose();
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);
    queueMicrotask(() => firstSelect?.focus());
    onCleanup(() => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    });
  });

  const updateDraft = (patch) => setDraft((current) => ({ ...current, ...patch }));

  return (
    <Show when={props.open}>
      <div class="media-library-filter-backdrop" onClick={props.onClose}>
        <section class="media-library-filter-dialog" role="dialog" aria-modal="true" aria-labelledby="media-library-filter-title" onClick={(event) => event.stopPropagation()}>
          <header>
            <div>
              <h3 id="media-library-filter-title">筛选素材</h3>
              <Show when={selectedCount() > 0}><span>已选择 {selectedCount()} 项</span></Show>
            </div>
            <button type="button" class="media-library-filter-close" onClick={props.onClose}>关闭</button>
          </header>

          <div class="media-library-filter-fields">
            <FilterSelect inputRef={(element) => { firstSelect = element; }} label="分析状态" value={draft().analysisStatus} onChange={(analysisStatus) => updateDraft({ analysisStatus })}>
              <option value="all">全部分析状态</option><option value="not_analyzed">未分析</option><option value="processing">处理中</option><option value="blocked">等待授权</option><option value="partial">部分完成</option><option value="ready">已完成</option><option value="stale">已过期</option><option value="failed">失败</option>
            </FilterSelect>
            <FilterSelect label="字幕类型" value={draft().subtitleMode} onChange={(subtitleMode) => updateDraft({ subtitleMode })}>
              <option value="all">全部字幕类型</option><option value="embedded">有字幕</option><option value="none">无字幕</option><option value="unknown">未识别</option>
            </FilterSelect>
            <FilterSelect label="素材时长" value={draft().duration} onChange={(duration) => updateDraft({ duration })}>
              <option value="all">全部时长</option><option value="under_1m">1 分钟内</option><option value="1m_5m">1～5 分钟</option><option value="5m_30m">5～30 分钟</option><option value="over_30m">30 分钟以上</option>
            </FilterSelect>
            <FilterSelect label="素材标签" value={draft().tag} onChange={(tag) => updateDraft({ tag })}>
              <option value="all">全部标签</option><For each={props.tags}>{(tag) => <option value={tag}>{tag}</option>}</For>
            </FilterSelect>
            <FilterSelect label="更新时间" value={draft().updated} onChange={(updated) => updateDraft({ updated })}>
              <option value="all">全部时间</option><option value="today">今天</option><option value="7d">近 7 天</option><option value="30d">近 30 天</option>
            </FilterSelect>
            <FilterSelect label="画面方向" value={draft().orientation} onChange={(orientation) => updateDraft({ orientation })}>
              <option value="all">全部方向</option><option value="landscape">横屏 16:9</option><option value="portrait">竖屏 9:16</option>
            </FilterSelect>
            <FilterSelect wide label="排序方式" value={draft().sort} onChange={(sort) => updateDraft({ sort })}>
              <option value="updated_desc">最近更新</option><option value="updated_asc">最早更新</option><option value="duration_desc">时长从长到短</option><option value="duration_asc">时长从短到长</option><option value="name_asc">名称 A-Z</option><option value="name_desc">名称 Z-A</option>
            </FilterSelect>
            <label class="media-library-archive-toggle is-wide"><input type="checkbox" checked={draft().includeArchived} onChange={(event) => updateDraft({ includeArchived: event.currentTarget.checked })} /><span>显示已归档素材</span></label>
          </div>

          <footer>
            <button type="button" class="media-library-filter-reset" disabled={selectedCount() === 0} onClick={() => setDraft(resetMediaLibraryFilters(draft()))}>重置</button>
            <div>
              <button type="button" class="secondary" onClick={props.onClose}>取消</button>
              <button type="button" class="primary" onClick={() => { props.onApply({ ...draft(), page: 1 }); props.onClose(); }}>应用筛选</button>
            </div>
          </footer>
        </section>
      </div>
    </Show>
  );
}

export default function MediaLibraryFilters(props) {
  const [open, setOpen] = createSignal(false);
  const count = createMemo(() => mediaLibraryActiveFilterCount(props.filters));
  return (
    <>
      <div class="media-library-filter-toolbar">
        <div class="media-library-search-wrap"><input value={props.filters.q} onInput={(event) => props.onSearch(event.currentTarget.value)} placeholder="搜索素材名称、对白、标签、文件名..." /></div>
        <button type="button" class={`media-library-filter-trigger ${count() ? "is-active" : ""}`} aria-haspopup="dialog" aria-expanded={open()} onClick={() => setOpen(true)}>
          <span>筛选</span><Show when={count() > 0}><strong>{count()}</strong></Show>
        </button>
      </div>
      <MediaLibraryFilterDialog open={open()} filters={props.filters} tags={props.tags} onClose={() => setOpen(false)} onApply={props.onApply} />
    </>
  );
}
