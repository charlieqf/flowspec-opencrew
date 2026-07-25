import { render } from "solid-js/web";
// @ts-ignore - App.jsx is the current OpenCrew shell implementation.
import App from "./App.jsx";
import "./styles/app.css";

render(() => <App />, document.getElementById("root")!);
