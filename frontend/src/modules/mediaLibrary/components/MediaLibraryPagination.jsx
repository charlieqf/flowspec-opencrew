import { Show } from "solid-js";

export default function MediaLibraryPagination(props) {
  const pages = () => Math.max(1, Math.ceil(props.total / props.filters.pageSize));
  return (
    <Show when={props.total > 0}>
      <div class="media-library-pagination">
        <span>共 {props.total} 条</span>
        <div><button type="button" disabled={props.filters.page <= 1} onClick={() => props.onPage(props.filters.page - 1)}>上一页</button><strong>{props.filters.page} / {pages()}</strong><button type="button" disabled={props.filters.page >= pages()} onClick={() => props.onPage(props.filters.page + 1)}>下一页</button></div>
        <select value={props.filters.pageSize} onChange={(event) => props.onPageSize(Number(event.currentTarget.value))} aria-label="每页数量"><option value="20">20 条/页</option><option value="50">50 条/页</option><option value="100">100 条/页</option></select>
      </div>
    </Show>
  );
}
