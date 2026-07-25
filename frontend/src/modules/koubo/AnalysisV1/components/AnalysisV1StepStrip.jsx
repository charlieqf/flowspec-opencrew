import { For } from "solid-js";
import { statusTone } from "../analysisV1Model";

export default function AnalysisV1StepStrip(props) {
  const steps = () => [
    { id: "00", name: "Session", status: props.hasTask ? "ready" : "idle" },
    { id: "02", name: "ASR", status: props.step02?.status || "idle" },
    { id: "02_01", name: "SRT Frame", status: props.step0201?.status || "idle" },
    { id: "Output", name: "Dialogue View", status: props.items?.length ? "completed" : "idle" },
  ];
  return <section class="analysis-v1-step-strip">
    <For each={steps()}>{(step) => (
      <div class={`analysis-v1-step is-${statusTone(step.status)}`}>
        <strong>{step.id}</strong>
        <span>{step.name}</span>
        <em>{step.status}</em>
      </div>
    )}</For>
  </section>;
}
