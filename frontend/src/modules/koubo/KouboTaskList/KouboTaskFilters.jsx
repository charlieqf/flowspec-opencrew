export default function KouboTaskFilters(props) {
  return (
    <div class="koubo-task-list-filters">
      <input
        value={props.filters().keyword}
        onInput={(event) => props.onChange({ keyword: event.currentTarget.value })}
        placeholder="搜索任务名、Task、Session、脚本..."
      />
      <select value={props.filters().mode} onChange={(event) => props.onChange({ mode: event.currentTarget.value })}>
        <option value="all">全部来源</option>
        <option value="video">视频分析</option>
        <option value="script">脚本生成</option>
      </select>
      <select value={props.filters().status} onChange={(event) => props.onChange({ status: event.currentTarget.value })}>
        <option value="all">全部状态</option>
        <option value="editable">可编辑</option>
        <option value="initializing">初始化中</option>
        <option value="running">运行中</option>
        <option value="failed">失败</option>
        <option value="draft">草稿</option>
      </select>
      <label class="koubo-task-list-archived-toggle">
        <input type="checkbox" checked={props.includeArchived()} onChange={(event) => props.onIncludeArchivedChange(event.currentTarget.checked)} />
        <span>显示归档</span>
      </label>
    </div>
  );
}
