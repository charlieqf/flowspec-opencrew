import { Show, createSignal } from "solid-js";

export function useConnectionTestsController() {
    const [connectionTests, setConnectionTests] = createSignal({});

    const testStateKey = (scope, provider) => `${scope}:${provider}`;
    const resetConnectionTest = (scope, provider) => {
        setConnectionTests((prev) => {
            const next = { ...prev };
            delete next[testStateKey(scope, provider)];
            return next;
        });
    };
    const updateConnectionTest = (scope, provider, patch) => {
        const key = testStateKey(scope, provider);
        setConnectionTests((prev) => ({ ...prev, [key]: { status: "idle", message: "", detail: "", expanded: false, ...(prev[key] ?? {}), ...patch } }));
    };
    const connectionTestState = (scope, provider) => connectionTests()[testStateKey(scope, provider)] ?? { status: "idle", message: "", detail: "", expanded: false };
    const renderConnectionTestControl = (scope, provider, model, unsaved, onTest) => {
        const state = connectionTestState(scope, provider);
        const disabled = unsaved || state.status === "testing" || !model;
        const label = unsaved ? "Save first" : state.status === "testing" ? "Testing..." : state.status === "success" ? "Success" : state.status === "failed" ? "Failed" : "Connection Test";
        return (<div class="connection-test-wrap" onClick={(event) => event.stopPropagation()}>
          <button class={`connection-test-button ${state.status}`} type="button" disabled={disabled} onClick={() => state.status === "failed" ? updateConnectionTest(scope, provider, { expanded: !state.expanded }) : void onTest()}>{label}</button>
          <Show when={state.status === "failed" && state.expanded}>
            <div class="connection-test-detail">
              <strong>{state.message || "Connection failed"}</strong>
              <p>{state.detail || "No error detail returned."}</p>
              <button type="button" onClick={() => void onTest()}>Retest</button>
            </div>
          </Show>
        </div>);
    };

    return {
        connectionTests,
        setConnectionTests,
        testStateKey,
        resetConnectionTest,
        updateConnectionTest,
        connectionTestState,
        renderConnectionTestControl,
    };
}
