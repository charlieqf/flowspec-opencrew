export const DEFAULT_NPC_SERVER_ADDR = "113.125.202.171:8024";
export const DEFAULT_NPC_MULTI_ACCOUNT_PATH = "~/.opencrew/npc/conf/multi_account.conf";
export const RETIRED_NAV_HASH_PREFIXES = ["#/sessions", "#/openflow", "#/openclip", "#/ocrebuild", "#/ocstoryboard"];
export function buildNpcConfPreview(config) {
    const lines = [
        "[common]",
        `server_addr=${config.server_addr}`,
        `conn_type=${config.conn_type}`,
        `vkey=${config.vkey}`,
        `auto_reconnection=${config.auto_reconnection ? "true" : "false"}`,
        `max_conn=${config.max_conn}`,
        `flow_limit=${config.flow_limit}`,
        `rate_limit=${config.rate_limit}`,
    ];
    if (config.basic_username && config.basic_password) {
        lines.push(`basic_username=${config.basic_username}`, `basic_password=${config.basic_password}`);
    }
    lines.push(
        `crypt=${config.crypt ? "true" : "false"}`,
        `compress=${config.compress ? "true" : "false"}`,
        `disconnect_timeout=${config.disconnect_timeout}`,
        "",
        `[${config.mode}]`,
        `mode=${config.mode}`,
        `target_addr=${config.target_addr}`,
        `server_port=${config.server_port}`,
        "",
    );
    return lines.join("\n");
}
export function buildMultiAccountPreview(line) {
    return `${line || "npc=npc.pwd"}\n`;
}
export function statusLabel(status) {
    const value = String(status ?? "idle");
    if (value === "connected")
        return "Connected";
    return value.replaceAll("_", " ");
}
export function statusVariant(status) {
    const value = String(status ?? "idle");
    if (["ready", "configured", "success", "verified"].includes(value))
        return "ready";
    if (["installed", "succeeded"].includes(value))
        return "installed";
    if (["available", "connected", "running"].includes(value))
        return "available";
    if (["unconfigured", "auth_required"].includes(value))
        return "unconfigured";
    if (["failed", "error"].includes(value))
        return "failed";
    return "idle";
}
export function dispatchWindowEvent(name) {
    try {
        window.dispatchEvent(new window.Event(name));
    }
    catch {
        const event = document.createEvent("Event");
        event.initEvent(name, true, true);
        window.dispatchEvent(event);
    }
}
export function StatusIcon(props) {
    const variant = statusVariant(props.status);
    if (variant === "ready") {
        return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="20 6 9 17 4 12"/>
      </svg>);
    }
    if (variant === "installed") {
        return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
        <polyline points="22 4 12 14.01 9 11.01"/>
      </svg>);
    }
    if (variant === "available") {
        return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10" fill="currentColor" opacity="0.2" stroke="none"/>
        <circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/>
      </svg>);
    }
    if (variant === "unconfigured" || variant === "failed") {
        return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>);
    }
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10"/>
      <polyline points="12 6 12 12 16 14"/>
    </svg>);
}
export function StatusBadge(props) {
    return (<span class={`status-tag tag-${statusVariant(props.status)}`}>
      <StatusIcon status={props.status}/>
      {statusLabel(props.status)}
    </span>);
}
export function probeLabel(candidate) {
    if (candidate.probe_status === "healthy") {
        const source = candidate.auth_source ? ` via ${candidate.auth_source}` : "";
        return candidate.version ? `healthy${source} (${candidate.version})` : `healthy${source}`;
    }
    if (candidate.probe_status === "auth_required") {
        return candidate.http_status ? `auth required (HTTP ${candidate.http_status})` : "auth required";
    }
    return candidate.error || candidate.probe_status || "unknown";
}
export function parseJson(text) {
    if (!text)
        return null;
    try {
        return JSON.parse(text);
    }
    catch {
        return null;
    }
}
export function parsePublishChecks(text) {
    if (!text)
        return [];
    try {
        const parsed = JSON.parse(text);
        if (!Array.isArray(parsed))
            return [];
        return parsed.flatMap((item) => {
            if (!item || typeof item !== "object")
                return [];
            const record = item;
            return [{
                    name: String(record.name ?? "check"),
                    ok: Boolean(record.ok),
                    message: String(record.message ?? ""),
                    category: String(record.category ?? "general"),
                    severity: String(record.severity ?? (Boolean(record.ok) ? "info" : "error")),
                    recommended_fix: String(record.recommended_fix ?? ""),
                }];
        });
    }
    catch {
        return [];
    }
}
export function toPublishConfigPayload(source) {
    return {
        status: String(source.status ?? "idle"),
        input_url: String(source.input_url ?? ""),
        normalized_url: String(source.normalized_url ?? ""),
        scheme: String(source.scheme ?? "https"),
        domain: String(source.domain ?? ""),
        path_prefix: String(source.path_prefix ?? "/"),
        deployment_mode: String(source.deployment_mode ?? "subdomain"),
        local_frontend_url: String(source.local_frontend_url ?? "http://127.0.0.1:18080/"),
        local_backend_api_url: String(source.local_backend_api_url ?? "http://127.0.0.1:8011/api/"),
        public_api_url: String(source.public_api_url ?? ""),
        allowed_hosts_hint: String(source.allowed_hosts_hint ?? ""),
        guide_markdown: String(source.guide_markdown ?? ""),
        nginx_config: String(source.nginx_config ?? ""),
        nps_config: String(source.nps_config ?? ""),
        message: String(source.message ?? "Waiting for URL input"),
        last_error: source.last_error == null ? null : String(source.last_error),
        test_detail: source.test_detail == null ? null : String(source.test_detail),
        updated_at: source.updated_at == null ? null : Number(source.updated_at),
        tested_at: source.tested_at == null ? null : Number(source.tested_at),
    };
}
export function summarizeNpcResult(result) {
    if (!result)
        return "-";
    const message = result.message;
    if (typeof message === "string" && message.trim())
        return message.trim();
    const error = result.error;
    if (typeof error === "string" && error.trim())
        return error.trim();
    return "-";
}
export function formatConsoleTime(value) {
    return new Date(value).toLocaleTimeString([], { hour12: false });
}
export function payloadTaskId(payload) {
    const parsed = parseJson(payload);
    const raw = parsed?.task_id;
    return typeof raw === "number" ? raw : null;
}
export function DiscoverIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="2" y="4" width="20" height="6" rx="2"/>
      <rect x="2" y="14" width="11" height="6" rx="2"/>
      <line x1="6" y1="7" x2="6.01" y2="7"/>
      <line x1="6" y1="17" x2="6.01" y2="17"/>
      <circle cx="17" cy="17" r="3"/>
      <line x1="19.5" y1="19.5" x2="22" y2="22"/>
    </svg>);
}
export function DetectEnvIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8"/>
      <line x1="21" y1="21" x2="16.65" y2="16.65"/>
      <polyline points="8 11 11 14 15 9"/>
    </svg>);
}
export function CheckIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
      <polyline points="22 4 12 14.01 9 11.01"/>
    </svg>);
}
export function SaveIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
      <polyline points="17 21 17 13 7 13 7 21"/>
      <polyline points="7 3 7 8 15 8"/>
    </svg>);
}
export function InstallIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M5 3h14"/>
      <path d="m18 13-6 6-6-6"/>
      <path d="M12 19V5"/>
    </svg>);
}
export function LoginIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/>
      <polyline points="10 17 15 12 10 7"/>
      <line x1="15" y1="12" x2="3" y2="12"/>
    </svg>);
}
export function LogoutIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
      <polyline points="14 17 9 12 14 7"/>
      <line x1="9" y1="12" x2="21" y2="12"/>
    </svg>);
}
export function CodeIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polyline points="16 18 22 12 16 6"/>
      <polyline points="8 6 2 12 8 18"/>
      <line x1="14" y1="4" x2="10" y2="20"/>
    </svg>);
}
export function PlayIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polygon points="6 4 20 12 6 20 6 4"/>
    </svg>);
}
export function TrashIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polyline points="3 6 5 6 21 6"/>
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
      <line x1="10" y1="11" x2="10" y2="17"/>
      <line x1="14" y1="11" x2="14" y2="17"/>
    </svg>);
}
export function CopyIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
    </svg>);
}
export function ClearIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/>
      <path d="M22 21H7"/>
      <path d="m5 11 9 9"/>
    </svg>);
}
export function CloseIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18"/>
      <line x1="6" y1="6" x2="18" y2="18"/>
    </svg>);
}
export function ExpandIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polyline points="15 3 21 3 21 9"/>
      <polyline points="9 21 3 21 3 15"/>
      <line x1="21" y1="3" x2="14" y2="10"/>
      <line x1="3" y1="21" x2="10" y2="14"/>
    </svg>);
}
export function SidebarToggleIcon(props) {
    return (<svg class={props.collapsed ? "is-collapsed" : ""} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M15 6l-6 6 6 6"/>
    </svg>);
}
export function ConnectionIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M10 14l-2.5 2.5a3.536 3.536 0 0 1-5-5l2.5-2.5"/>
      <path d="M14 10l2.5-2.5a3.536 3.536 0 0 1 5 5l-2.5 2.5"/>
      <line x1="10" y1="14" x2="14" y2="10"/>
    </svg>);
}
export function AudioWaveIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M2 10v3"/>
      <path d="M6 6v11"/>
      <path d="M10 3v18"/>
      <path d="M14 8v7"/>
      <path d="M18 5v13"/>
      <path d="M22 10v3"/>
    </svg>);
}
export function ImageModelIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>
      <circle cx="9" cy="9" r="2"/>
      <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>
    </svg>);
}
export function VideoModelIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="3" y="6" width="13" height="12" rx="2"/>
      <path d="M16 10l5-3v10l-5-3"/>
      <path d="M7 10h5"/>
      <path d="M7 14h3"/>
    </svg>);
}
export function FilmIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect width="18" height="18" x="3" y="3" rx="2"/>
      <path d="M7 3v18"/>
      <path d="M3 7.5h4"/>
      <path d="M3 12h18"/>
      <path d="M3 16.5h4"/>
      <path d="M17 3v18"/>
      <path d="M17 7.5h4"/>
      <path d="M17 16.5h4"/>
    </svg>);
}
export function PersonIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M20 21a8 8 0 0 0-16 0"/>
      <circle cx="12" cy="8" r="4"/>
    </svg>);
}
export function ShareIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="18" cy="5" r="3"/>
      <circle cx="6" cy="12" r="3"/>
      <circle cx="18" cy="19" r="3"/>
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
    </svg>);
}
export function StopIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9"/>
      <rect x="9" y="9" width="6" height="6" rx="1"/>
    </svg>);
}
export function ArrowLeftIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <line x1="19" y1="12" x2="5" y2="12"/>
      <polyline points="12 19 5 12 12 5"/>
    </svg>);
}
export function GitBranchIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="6" cy="6" r="2.5"/>
      <circle cx="18" cy="18" r="2.5"/>
      <circle cx="18" cy="6" r="2.5"/>
      <path d="M8.5 6h5A2.5 2.5 0 0 1 16 8.5V15"/>
      <path d="M8.5 6v0A2.5 2.5 0 0 0 11 8.5H16"/>
    </svg>);
}
export function ClockCounterClockwiseIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M3 12a9 9 0 1 0 3-6.7"/>
      <polyline points="3 4 3 12 11 12"/>
      <polyline points="12 7 12 12 16 14"/>
    </svg>);
}
export function ArrowsClockwiseIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polyline points="23 4 23 10 17 10"/>
      <polyline points="1 20 1 14 7 14"/>
      <path d="M3.5 9A9 9 0 0 1 19 5l4 5"/>
      <path d="M20.5 15A9 9 0 0 1 5 19l-4-5"/>
    </svg>);
}
export function HourglassIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M7 3h10"/>
      <path d="M7 21h10"/>
      <path d="M8 3c0 4 4 4.5 4 9s-4 5-4 9"/>
      <path d="M16 3c0 4-4 4.5-4 9s4 5 4 9"/>
    </svg>);
}
export function UploadIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
      <polyline points="17 8 12 3 7 8"/>
      <line x1="12" y1="3" x2="12" y2="15"/>
    </svg>);
}
export function ZipIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M8 3h8l5 5v11a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>
      <path d="M16 3v5h5"/>
      <path d="M12 7h.01"/>
      <path d="M12 10h.01"/>
      <path d="M12 13h.01"/>
      <rect x="10" y="15" width="4" height="4" rx="1"/>
    </svg>);
}
export function RobotIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="5" y="8" width="14" height="11" rx="3"/>
      <path d="M12 4v4"/>
      <path d="M9 2h6"/>
      <circle cx="9.5" cy="13" r="1" fill="currentColor" stroke="none"/>
      <circle cx="14.5" cy="13" r="1" fill="currentColor" stroke="none"/>
      <path d="M9 16h6"/>
    </svg>);
}
export function FileIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M8 3h8l5 5v11a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>
      <path d="M16 3v5h5"/>
    </svg>);
}
export function MediaLibraryIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2"/>
      <path d="M8 4v16M16 4v16M3 9h5M3 15h5M16 9h5M16 15h5"/>
      <path d="m10 9 4 3-4 3V9z"/>
    </svg>);
}
export function PriceListIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M5 6h14"/>
      <path d="M5 12h14"/>
      <path d="M5 18h14"/>
      <path d="M8 4v16" opacity="0.45"/>
      <path d="M16 4v16" opacity="0.45"/>
    </svg>);
}
export function BillingIcon() {
    return (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/>
      <path d="M9 8h6"/>
      <path d="M9 12h6"/>
      <path d="M9 16h3"/>
    </svg>);
}
export function formatBytes(value) {
    if (value < 1024)
        return `${value} B`;
    if (value < 1024 * 1024)
        return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
export function formatGigabytesFromMb(value) {
    return `${((value ?? 0) / 1024).toFixed(1)} GB`;
}
export function formatDateTime(value) {
    if (!value)
        return "-";
    return new Date(value).toLocaleString([], { hour12: false });
}
export function parseSessionHash(hashValue) {
    const hash = hashValue ?? window.location.hash ?? "";
    const match = hash.match(/^#\/sessions\/task\/(\d+)/);
    return match ? Number(match[1]) : null;
}
export function parseMeteringTaskHash(hashValue) {
    const hash = hashValue ?? window.location.hash ?? "";
    const match = hash.match(/^#\/metering\/task\/(\d+)/);
    return match ? Number(match[1]) : null;
}
export function parseMeteringHash(hashValue) {
    const hash = hashValue ?? window.location.hash ?? "";
    const taskId = parseMeteringTaskHash(hash);
    if (taskId)
        return { tab: "task", taskId };
    if (/^#\/metering\/global(?:$|[/?#])/.test(hash))
        return { tab: "global", taskId: null };
    if (/^#\/metering\/prices(?:$|[/?#])/.test(hash))
        return { tab: "price", taskId: null };
    if (/^#\/metering(?:$|\/$)/.test(hash))
        return { tab: "task", taskId: null };
    return null;
}
export function linkifyText(text) {
    const pattern = /(https?:\/\/\S+)/g;
    const parts = [];
    let lastIndex = 0;
    for (const match of text.matchAll(pattern)) {
        const start = match.index ?? 0;
        if (start > lastIndex)
            parts.push({ type: "text", value: text.slice(lastIndex, start) });
        parts.push({ type: "link", value: match[0] });
        lastIndex = start + match[0].length;
    }
    if (lastIndex < text.length)
        parts.push({ type: "text", value: text.slice(lastIndex) });
    return parts.length ? parts : [{ type: "text", value: text }];
}
export function canCancelTask(status) {
    return ["running", "queued", "waiting_input"].includes(String(status || ""));
}
export function canRerunTask(status) {
    return !["running", "queued"].includes(String(status || ""));
}
export function sessionMessageTone(role) {
    const value = String(role ?? "assistant").toLowerCase();
    return value === "user" ? "user" : "assistant";
}
export function sessionMessageLabel(role) {
    const value = String(role ?? "assistant").toLowerCase();
    if (value === "user")
        return "USER";
    if (value === "system")
        return "SYSTEM";
    return "ASSISTANT";
}
export function sessionMessageAvatar(role) {
    const value = String(role ?? "assistant").toLowerCase();
    if (value === "user")
        return "U";
    if (value === "system")
        return "S";
    return "A";
}
export function formatSessionEventPreview(event) {
    return JSON.stringify(event.payload ?? {}, null, 2);
}
export function normalizeAssistantText(text) {
    const value = String(text ?? "");
    return value.replaceAll("JSON_RESULT=", "");
}
export function isSystemLogEvent(kind) {
    return !["assistant.final", "message.part.delta", "message.part.updated", "message.updated"].includes(kind);
}
export function parseSessionReferenceText(text) {
    const match = text.trim().match(/^#?(\d+)(?:\s+(.*))?$/);
    if (!match)
        return { sessionId: null, trailing: "" };
    return { sessionId: Number(match[1]), trailing: (match[2] || "").trim() };
}
export function summarizeSessionEvent(event) {
    const payload = event.payload || {};
    const kind = event.kind;
    if (kind === "session.created")
        return "Task created";
    if (kind === "session.rerun")
        return "Session rerun started";
    if (kind === "session.bound")
        return `Bound to OpenCode session ${String(payload.opencode_session_id || "")}`.trim();
    if (kind === "user.message")
        return `User sent: ${String(payload.text || "").slice(0, 80)}`;
    if (kind === "assistant.final")
        return `Assistant replied: ${String(payload.text || "").slice(0, 80)}`;
    if (kind === "file.uploaded")
        return `Uploaded file ${String(payload.name || payload.path || "")}`.trim();
    if (kind === "file.downloaded")
        return `Downloaded file ${String(payload.path || "")}`.trim();
    if (kind === "openflow.reference_video.staged")
        return `Reference video staged: ${String(payload.target || payload.source || "")}`.trim();
    if (kind === "openflow.analysis.started")
        return payload.rerun ? "OpenFlow rerun started" : "OpenFlow analysis started";
    if (kind === "openflow.analysis.config.saved")
        return "OpenFlow config saved";
    if (kind === "openflow.analysis.prompt.generated")
        return `Prompt generated with ${String(payload.provider || "-")}/${String(payload.model || "-")}`;
    if (kind === "openclip.current_skill.injected")
        return "OpenClip Run injected Current Skill only";
    if (kind === "openclip.analysis.started")
        return `OpenClip analysis started with ${String(payload.skill_version_id || "-")}`;
    if (kind === "ocrebuild.asset_image.requested")
        return `Image API call requested: ${String(payload.shot_id || "-")} / ${String(payload.scene_mark_id || "-")} / ${payload.use_reference_image ? "with reference" : "prompt only"}`;
    if (kind === "ocrebuild.asset_image.provider_call.started")
        return `Image provider call started: ${String(payload.provider || "-")}/${String(payload.model || "-")}`;
    if (kind === "ocrebuild.asset_image.heartbeat")
        return `Image API heartbeat #${String(payload.heartbeat || "-")} (${String(payload.elapsed_seconds || 0)}s)`;
    if (kind === "ocrebuild.asset_image.generated")
        return `Image generated: ${String(payload.provider || "-")}/${String(payload.model || "-")} -> ${String(payload.output || "-")}`;
    if (kind === "ocrebuild.asset_image.failed")
        return `Image generation failed: ${String(payload.detail || "unknown error")}`;
    if (kind === "ocrebuild.asset_image.prompt_refine.requested")
        return `Prompt refine requested: ${String(payload.provider || "-")}/${String(payload.image_model || "-")}`;
    if (kind === "ocrebuild.asset_image.prompt_refine.run_model.started")
        return `Prompt refine Run Model started: ${String(payload.run_model_provider || "-")}/${String(payload.run_model_id || "-")}`;
    if (kind === "ocrebuild.asset_image.prompt_refine.completed")
        return `Prompt refined with docs: ${String(payload.provider || "-")}/${String(payload.image_model || "-")}`;
    if (kind === "ocrebuild.asset_image.prompt_refine.failed")
        return `Prompt refine failed: ${String(payload.detail || "unknown error")}`;
    if (kind === "ocrebuild.asset_image.workflow.started")
        return `Image compare workflow started: round ${String(payload.round || "-")}`;
    if (kind === "ocrebuild.asset_image.workflow.round_completed")
        return `Image compare round completed: round ${String(payload.round || "-")}`;
    if (kind === "ocrebuild.asset_image.workflow.finalized")
        return `Image compare finalized: ${String(payload.output || "-")}`;
    if (kind === "system.message" && payload.kind === "current_skill")
        return "Run input policy: Current Skill only";
    if (kind === "session.cancelled")
        return "Task cancelled";
    if (kind === "session.error")
        return `Task error: ${String(payload.message || "unknown error")}`;
    if (kind === "todo.snapshot") {
        const items = Array.isArray(payload.items) ? payload.items.length : 0;
        return `Todo snapshot updated (${items} items)`;
    }
    if (kind === "session.idle")
        return "Session is idle and waiting for input";
    if (kind === "session.status") {
        const status = String(((payload.status || {}).type) || "running");
        return `Session status changed: ${status}`;
    }
    if (kind === "message.part.delta")
        return "Streaming response chunk received";
    if (kind === "message.part.updated")
        return "Message part updated";
    if (kind === "message.updated")
        return "Message updated";
    if (kind === "session.diff")
        return "Workspace diff updated";
    return kind.replaceAll(".", " ");
}
export function isOCRebuildImageEvent(event) {
    return String(event?.kind || "").startsWith("ocrebuild.asset_image.");
}
export function imageApiCallKey(event) {
    const payload = event?.payload || {};
    if (String(event?.kind || "").startsWith("ocrebuild.asset_image.prompt_refine."))
        return String(payload.workflow_id || "workflow") + `:prompt:${payload.provider || "provider"}:${payload.image_model || "model"}:${payload.round || 1}`;
    if (String(event?.kind || "").startsWith("ocrebuild.asset_image.workflow."))
        return String(payload.workflow_id || "workflow") + `:workflow:${payload.round || ""}:${event.kind}`;
    return String(payload.api_call_id || `${payload.task_id || "task"}:${payload.shot_id || "shot"}:${payload.scene_mark_id || "scene"}:${payload.role || "single"}`);
}
export function summarizeImageApiCall(events) {
    const ordered = [...events].sort((a, b) => Number(a.created_at || 0) - Number(b.created_at || 0));
    const latest = ordered[ordered.length - 1] || {};
    const latestPayload = latest.payload || {};
    const requested = ordered.find((event) => event.kind === "ocrebuild.asset_image.requested")?.payload || {};
    const started = ordered.find((event) => event.kind === "ocrebuild.asset_image.provider_call.started")?.payload || {};
    const heartbeats = ordered.filter((event) => event.kind === "ocrebuild.asset_image.heartbeat");
    const latestHeartbeat = heartbeats[heartbeats.length - 1]?.payload || null;
    const generated = ordered.find((event) => event.kind === "ocrebuild.asset_image.generated")?.payload || null;
    const failed = ordered.find((event) => event.kind === "ocrebuild.asset_image.failed")?.payload || null;
    const promptRefined = ordered.find((event) => event.kind === "ocrebuild.asset_image.prompt_refine.completed")?.payload || null;
    const promptRunStarted = ordered.find((event) => event.kind === "ocrebuild.asset_image.prompt_refine.run_model.started")?.payload || null;
    const workflowFinalized = ordered.find((event) => event.kind === "ocrebuild.asset_image.workflow.finalized")?.payload || null;
    const base = { ...requested, ...started, ...latestPayload };
    if (promptRefined || promptRunStarted) {
        const promptBase = { ...(promptRunStarted || {}), ...(promptRefined || {}), ...latestPayload };
        const status = failed ? "failed" : promptRefined ? "completed" : promptRunStarted ? "calling" : "requested";
        return {
            time: Number(latest.created_at || Date.now()),
            level: failed ? "error" : promptRefined ? "info" : "warn",
            source: "API Call",
            message: [`${status.toUpperCase()} prompt refine`, `${promptBase.provider || "-"}/${promptBase.image_model || "-"}`, `Run ${promptBase.run_model_provider || "-"}/${promptBase.run_model_id || "-"}`, promptBase.docs_url ? "docs fetched" : "docs pending"].filter(Boolean).join(" | "),
            expandable: true,
            key: imageApiCallKey(latest),
            details: {
                status,
                workflow_id: promptBase.workflow_id || "",
                provider: promptBase.provider || "",
                image_model: promptBase.image_model || "",
                run_model_provider: promptBase.run_model_provider || "",
                run_model_id: promptBase.run_model_id || "",
                docs_url: promptBase.docs_url || "",
                docs_fetched_realtime: Boolean(promptBase.docs_fetched_realtime),
                temporary: Boolean(promptBase.temporary),
                writes_asset_json: Boolean(promptBase.writes_asset_json),
                writes_database: Boolean(promptBase.writes_database),
                prompt_length: promptBase.prompt_length || "",
                prompt_preview: promptBase.prompt_preview || "",
                error: failed?.detail || "",
            },
        };
    }
    if (workflowFinalized) {
        return {
            time: Number(latest.created_at || Date.now()),
            level: "info",
            source: "API Call",
            message: `FINALIZED image compare workflow | ${workflowFinalized.shot_id || "-"} / ${workflowFinalized.scene_mark_id || "-"} -> ${workflowFinalized.output || "-"}`,
            expandable: true,
            key: imageApiCallKey(latest),
            details: workflowFinalized,
        };
    }
    const elapsed = generated?.elapsed_seconds || failed?.elapsed_seconds || latestPayload.elapsed_seconds || "";
    const status = failed ? "failed" : generated ? "completed" : started.provider ? "calling" : "requested";
    const provider = base.provider && base.model ? `${base.provider}/${base.model}` : "provider pending";
    const target = `${base.shot_id || "-"} / ${base.scene_mark_id || "-"} / ${base.role || "single"}`;
    const outputPath = generated?.output_path || base.output_path || "";
    const heartbeatLabel = latestHeartbeat ? `heartbeat #${latestHeartbeat.heartbeat} (${latestHeartbeat.elapsed_seconds}s)` : "";
    const message = [`${status.toUpperCase()} image API call`, target, provider, base.use_reference_image ? "with reference" : "prompt only", heartbeatLabel || (elapsed ? `${elapsed}s` : ""), outputPath ? `-> ${outputPath}` : ""].filter(Boolean).join(" | ");
    return {
        time: Number(latest.created_at || Date.now()),
        level: failed ? "error" : generated ? "info" : "warn",
        source: "API Call",
        message,
        expandable: true,
        key: imageApiCallKey(latest),
        details: {
            status,
            api_call_id: base.api_call_id || "",
            task_id: base.task_id || "",
            session_id: base.session_id || "",
            shot_id: base.shot_id || "",
            scene_mark_id: base.scene_mark_id || "",
            role: base.role || "",
            provider: base.provider || "",
            model: base.model || "",
            endpoint: base.endpoint || "",
            method: base.method || "POST",
            use_reference_image: Boolean(base.use_reference_image),
            workspace_dir: base.workspace_dir || "",
            reference_path: base.reference_path || "",
            output: base.output || generated?.output || "",
            output_path: outputPath,
            elapsed_seconds: elapsed,
            heartbeat_count: heartbeats.length,
            latest_heartbeat: latestHeartbeat ? `#${latestHeartbeat.heartbeat} (${latestHeartbeat.elapsed_seconds}s)` : "",
            last_heartbeat_at: heartbeats.length ? formatConsoleTime(heartbeats[heartbeats.length - 1].created_at) : "",
            prompt_length: base.prompt_length || "",
            prompt_preview: base.prompt_preview || "",
            error: failed?.detail || "",
        },
    };
}
export function debugDetailRows(details) {
    return Object.entries(details || {})
        .filter(([, value]) => value !== "" && value !== null && value !== undefined)
        .map(([key, value]) => ({ key, value: typeof value === "boolean" ? String(value) : String(value) }));
}
