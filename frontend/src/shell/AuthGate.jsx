import { Show } from "solid-js";

export default function AuthGate(props) {
    const loading = () => props.authState().loading;
    const configured = () => props.authState().configured;
    return (<section class="auth-gate">
      <div class="auth-panel">
        <div class="brand auth-brand">OpenCrew</div>
        <h1>{loading() ? "Checking authentication" : configured() ? "Sign in" : "Create admin password"}</h1>
        <p>{loading() ? "Checking the current OpenCrew session." : configured() ? "Enter the OpenCrew application password." : "Set the local appliance password before continuing."}</p>
        <Show when={props.authError()}>
          <div class="banner bad">{props.authError()}</div>
        </Show>
        <input type="password" placeholder="Password" value={props.authPassword()} onInput={(event) => props.setAuthPassword(event.currentTarget.value)} onKeyDown={(event) => {
            if (event.key === "Enter" && !loading() && !props.authBusy())
                void props.submitAuth();
        }}/>
        <button type="button" disabled={loading() || props.authBusy()} onClick={() => void props.submitAuth()}>{loading() ? "Checking..." : props.authBusy() ? "Working..." : configured() ? "Sign in" : "Create password"}</button>
      </div>
    </section>);
}
