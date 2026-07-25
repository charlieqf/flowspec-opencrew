export const FALLBACK_USD_CNY_RATE = 7.1;

export const MEDIA_PRICE_POINTS = [];

export const LIPSYNC_PRICE_COMPARISON = [];

export function formatCurrencyAmount(amount, currency) {
    const prefix = currency === "USD" ? "$" : "¥";
    if (amount >= 100)
        return `${prefix}${amount.toFixed(0)}`;
    if (amount >= 1)
        return `${prefix}${amount.toFixed(2).replace(/\.00$/, "")}`;
    return `${prefix}${amount.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`;
}

export function formatCnyAmount(amount) {
    return formatCurrencyAmount(amount, "CNY");
}

export function unitLabel(unit) {
    if (unit === "second")
        return "秒";
    if (unit === "image")
        return "张";
    if (unit === "video_second")
        return "视频秒";
    if (unit === "audio_second")
        return "音频秒";
    if (unit === "bean")
        return "豆";
    if (unit === "five_second")
        return "5秒";
    if (unit === "input_token")
        return "输入 token";
    if (unit === "output_token")
        return "输出 token";
    if (unit === "request")
        return "请求";
    return unit;
}

export function formatMicrosUsd(value) {
    const amount = Number(value || 0) / 1000000;
    if (Math.abs(amount) >= 100)
        return `$${amount.toFixed(0)}`;
    if (Math.abs(amount) >= 1)
        return `$${amount.toFixed(2)}`;
    return `$${amount.toFixed(4)}`;
}

export function formatOptionalMicrosUsd(value, available = true) {
    if (!available && Number(value || 0) === 0)
        return "-";
    return formatMicrosUsd(value);
}

export function formatUnitMap(units) {
    const entries = Object.entries(units || {}).filter(([, value]) => Number(value || 0) > 0);
    if (entries.length === 0)
        return "-";
    return entries.map(([key, value]) => `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 })} ${unitLabel(key)}`).join(" · ");
}

export function formatPriceLines(lines) {
    const items = Array.isArray(lines) ? lines : [];
    if (items.length === 0)
        return "-";
    const priced = items.filter((line) => Number(line.provider_unit_cost_micros || 0) > 0 || Number(line.customer_unit_price_micros || 0) > 0);
    const displayItems = priced.length > 0 ? priced : items;
    return displayItems
        .slice(0, 3)
        .map((line) => `${formatMicrosUsd(line.provider_unit_cost_micros)}/${unitLabel(line.unit_key)} → ${formatMicrosUsd(line.customer_unit_price_micros)}`)
        .join(" · ");
}

export function costBasisLabel(value) {
    if (value === "actual")
        return "实际";
    if (value === "estimated")
        return "估算";
    return "未定价";
}

export function meteringStatusLabel(status) {
    const value = String(status ?? "unknown");
    if (["ok", "success", "succeeded", "completed"].includes(value))
        return "成功";
    if (["failed", "error"].includes(value))
        return "失败";
    if (["running", "pending"].includes(value))
        return "运行中";
    if (value === "unknown")
        return "未知";
    return value.replaceAll("_", " ");
}

export function meteringTaskTitle(row) {
    const taskId = String(row?.task_id ?? "").trim();
    const title = String(row?.title ?? "").trim();
    if (!row?.session_id && title === `Task #${taskId}`)
        return "仅有计费记录";
    if (!title || title === `Task #${taskId}`)
        return `Task #${taskId}`;
    return `标题：${title}`;
}

export function meteringTaskMeta(row) {
    const parts = [];
    if (row?.session_id)
        parts.push(`Session #${row.session_id}`);
    else if (row?.has_usage)
        parts.push("未找到任务元数据");
    parts.push(meteringStatusLabel(row?.task_status || row?.session_status));
    if (row?.latest_attempt_no)
        parts.push(`第 ${row.latest_attempt_no} 次运行`);
    if (row?.latest_attempt_id)
        parts.push(`attempt #${row.latest_attempt_id}`);
    const latestTime = formatMeteringTime(row?.latest_activity_at ?? row?.updated_at ?? row?.created_at);
    if (latestTime !== "-")
        parts.push(`最近 ${latestTime}`);
    return parts.join(" · ") || "-";
}

export function meteringWarningMessage(warning) {
    const code = String(warning?.code || "");
    const rowCount = Number(warning?.row_count || 0);
    if (code === "latest_attempt_has_no_usage")
        return `最新运行 attempt #${warning?.attempt_id || "-"} 在当前范围内没有计费记录。`;
    if (code === "usage_after_latest_start_attributed_to_older_attempt")
        return `有 ${rowCount || "部分"} 条计费记录发生在最新运行开始之后，但仍归属到更早的运行。`;
    if (code === "usage_missing_attempt_id")
        return `有 ${rowCount || "部分"} 条计费记录缺少 attempt ID，可能无法准确归入某次运行。`;
    if (code === "unpriced_usage_rows")
        return `有 ${rowCount || "部分"} 条记录没有供应商实际成本，也没有命中本地估算价格。`;
    if (code === "usage_missing_billable_units")
        return `有 ${rowCount || "部分"} 条记录缺少标准化用量，成本解读时需要回看原始记录。`;
    return warning?.message || code || "计费数据存在需要注意的情况。";
}

export function formatMeteringTime(value) {
    const timestamp = Number(value || 0);
    if (!timestamp)
        return "-";
    return new Date(timestamp).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
