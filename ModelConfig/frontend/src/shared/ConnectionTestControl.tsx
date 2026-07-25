import type { Accessor } from "solid-js";
import { Show } from "solid-js";
import type { ConnectionTestState } from "./types";

export function testStateKey(scope: string, provider: string) {
  return `${scope}:${provider}`;
}

export function defaultConnectionTestState(): ConnectionTestState {
  return { status: "idle", message: "", detail: "", expanded: false };
}

export function ConnectionTestControl(props: {
  state: Accessor<ConnectionTestState>;
  unsaved: boolean;
  model: string;
  onTest: () => void;
  onToggle: () => void;
}) {
  const disabled = () => props.unsaved || props.state().status === "testing" || !props.model;
  const label = () => props.unsaved ? "Save first" : props.state().status === "testing" ? "Testing..." : props.state().status === "success" ? "Success" : props.state().status === "failed" ? "Failed" : "Connection Test";
  return (
    <div class="connection-test-wrap" onClick={(event) => event.stopPropagation()}>
      <button class={`connection-test-button ${props.state().status}`} type="button" disabled={disabled()} onClick={() => props.state().status === "failed" ? props.onToggle() : props.onTest()}>{label()}</button>
      <Show when={props.state().status === "failed" && props.state().expanded}>
        <div class="connection-test-detail">
          <strong>{props.state().message || "Connection failed"}</strong>
          <p>{props.state().detail || "No error detail returned."}</p>
          <button type="button" onClick={() => props.onTest()}>Retest</button>
        </div>
      </Show>
    </div>
  );
}

