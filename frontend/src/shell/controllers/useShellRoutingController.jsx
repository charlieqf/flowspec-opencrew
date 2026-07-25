import { untrack, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { RETIRED_NAV_HASH_PREFIXES, parseMeteringHash } from "../appShellUtils.jsx";

export function useShellRoutingController(options) {
    const authState = options.authState;
    const authReady = options.authReady;
    const onMeteringRoute = options.onMeteringRoute ?? (() => {});
    const onClearSessionSelection = options.onClearSessionSelection ?? (() => {});

    const [activeNav, setActiveNav] = createSignal("analysis-v1");
    const [routeHash, setRouteHash] = createSignal(window.location.hash || "");

    const canManageConnection = createMemo(() => !authState().enabled || authState().role === "admin" || Boolean(authState().capabilities?.can_manage_connection));
    const canViewMetering = createMemo(() => !authState().enabled || authState().role === "admin" || Boolean(authState().capabilities?.can_view_metering));
    const roleAccess = createMemo(() => ({ isAdmin: canManageConnection() }));
    const navAllowed = (nav) => nav === "connection" ? canManageConnection() : nav === "metering" ? canViewMetering() : true;
    const statusCanManageConnection = (status) => !status.enabled || status.role === "admin" || Boolean(status.capabilities?.can_manage_connection);
    const statusCanViewMetering = (status) => !status.enabled || status.role === "admin" || Boolean(status.capabilities?.can_view_metering);
    const isRetiredNavHash = (hashValue) => RETIRED_NAV_HASH_PREFIXES.some((prefix) => String(hashValue || "").startsWith(prefix));
    const goToBusinessHome = () => {
        setActiveNav("analysis-v1");
        if (!window.location.hash || window.location.hash === "#" || window.location.hash.startsWith("#/metering") || isRetiredNavHash(window.location.hash))
            window.location.hash = "#/analysis-v1/tasks";
    };
    const applyRoleRoute = (status, hashValue = window.location.hash || "") => {
        if (status.enabled && !status.authenticated)
            return;
        if (isRetiredNavHash(hashValue)) {
            goToBusinessHome();
            return;
        }
        if (!hashValue) {
            if (!statusCanManageConnection(status))
                goToBusinessHome();
            return;
        }
        if (hashValue.startsWith("#/metering") && !statusCanViewMetering(status))
            goToBusinessHome();
        else if (hashValue === "#" && !statusCanManageConnection(status))
            goToBusinessHome();
    };
    const syncActiveNavFromHash = (hashValue = window.location.hash || "") => {
        if (hashValue.startsWith("#/media-library"))
            setActiveNav("media-library");
        else if (hashValue.startsWith("#/dance-mimic"))
            setActiveNav("dance-mimic");
        else if (hashValue.startsWith("#/talking-head"))
            setActiveNav("talking-head");
        else if (hashValue.startsWith("#/analysis-v1"))
            setActiveNav("analysis-v1");
        else if (hashValue.startsWith("#/koubo-storyboard"))
            setActiveNav("koubo-storyboard");
        else if (hashValue.startsWith("#/koubo-asset-library"))
            setActiveNav("koubo-asset-library");
        else if (hashValue.startsWith("#/metering") && canViewMetering())
            setActiveNav("metering");
    };
    const syncRouteHashFromExternalReplace = (nextHash) => {
        setRouteHash(nextHash);
    };

    onMount(() => {
        const initialHash = window.location.hash || "";
        setRouteHash(initialHash);
        const syncHashNav = () => syncActiveNavFromHash(window.location.hash || "");
        window.addEventListener("hashchange", syncHashNav);
        onCleanup(() => window.removeEventListener("hashchange", syncHashNav));
        if (isRetiredNavHash(initialHash)) {
            goToBusinessHome();
        }
        else if (initialHash.startsWith("#/media-library")) {
            setActiveNav("media-library");
        }
        else if (initialHash.startsWith("#/analysis-v1")) {
            setActiveNav("analysis-v1");
        }
        else if (initialHash.startsWith("#/koubo-tasks")) {
            setActiveNav("koubo-task-list");
        }
        else if (initialHash.startsWith("#/dance-mimic")) {
            setActiveNav("dance-mimic");
        }
        else if (initialHash.startsWith("#/talking-head")) {
            setActiveNav("talking-head");
        }
        else if (initialHash.startsWith("#/koubo-storyboard")) {
            setActiveNav("koubo-storyboard");
        }
        else if (initialHash.startsWith("#/koubo-asset-library")) {
            setActiveNav("koubo-asset-library");
        }
        else if (initialHash.startsWith("#/metering")) {
            setActiveNav("metering");
            onMeteringRoute(parseMeteringHash(initialHash));
        }

        const onHashChange = () => {
            const nextHash = window.location.hash || "";
            if (isRetiredNavHash(nextHash)) {
                goToBusinessHome();
                return;
            }
            if (!nextHash) {
                if (!canManageConnection() || activeNav() !== "connection")
                    setActiveNav("analysis-v1");
                setRouteHash(nextHash);
                onClearSessionSelection();
                return;
            }
            if (nextHash.startsWith("#/metering") && !canViewMetering()) {
                goToBusinessHome();
                return;
            }
            setRouteHash(nextHash);
            if (nextHash.startsWith("#/media-library")) {
                setActiveNav("media-library");
            }
            else if (nextHash.startsWith("#/analysis-v1")) {
                setActiveNav("analysis-v1");
            }
            else if (nextHash.startsWith("#/koubo-tasks")) {
                setActiveNav("koubo-task-list");
            }
            else if (nextHash.startsWith("#/dance-mimic")) {
                setActiveNav("dance-mimic");
            }
            else if (nextHash.startsWith("#/talking-head")) {
                setActiveNav("talking-head");
            }
            else if (nextHash.startsWith("#/koubo-storyboard")) {
                setActiveNav("koubo-storyboard");
            }
            else if (nextHash.startsWith("#/koubo-asset-library")) {
                setActiveNav("koubo-asset-library");
            }
            else if (nextHash.startsWith("#/metering")) {
                if (canViewMetering()) {
                    setActiveNav("metering");
                    void onMeteringRoute(parseMeteringHash(nextHash), { loadTask: true });
                }
                else
                    goToBusinessHome();
            }
        };
        window.addEventListener("hashchange", onHashChange);
        onCleanup(() => {
            window.removeEventListener("hashchange", onHashChange);
        });
    });

    createEffect(() => {
        if (!authReady() || activeNav() !== "metering" || !canViewMetering())
            return;
        const route = parseMeteringHash(window.location.hash || routeHash());
        if (!route)
            return;
        onMeteringRoute(route);
    });
    createEffect(() => {
        if (!authReady())
            return;
        // Track ONLY authReady + routeHash. Reading activeNav() reactively
        // here made the effect re-run synchronously on setActiveNav, with
        // the hash not yet cleared, the first Connection click was bounced
        // straight back to the hash-derived nav. routeHash is the reactive
        // mirror updated by the hashchange listener; "" is a real state.
        const hashValue = routeHash();
        syncActiveNavFromHash(hashValue);
        if (!navAllowed(untrack(activeNav)))
            goToBusinessHome();
    });

    return {
        activeNav,
        setActiveNav,
        routeHash,
        setRouteHash,
        canManageConnection,
        canViewMetering,
        roleAccess,
        navAllowed,
        statusCanManageConnection,
        statusCanViewMetering,
        isRetiredNavHash,
        goToBusinessHome,
        applyRoleRoute,
        syncActiveNavFromHash,
        syncRouteHashFromExternalReplace,
    };
}
