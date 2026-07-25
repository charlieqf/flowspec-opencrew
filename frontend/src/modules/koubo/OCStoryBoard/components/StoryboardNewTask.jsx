export function StoryboardNewTask(props) {
  return <div class="ocstoryboard-new">
    <form onSubmit={props.onJsonSubmit}>
      <section class="panel ocstoryboard-upload-panel">
        <div class="ocstoryboard-section-title"><h2>Structured StoryBoard</h2><p>上传 JSON，保留 Shot / Scene 层级、名称和旁白文本。</p></div>
        <label class="openflow-field"><span>JSON</span><input name="storyboard" type="file" accept=".json,application/json" required /></label>
        <label class="openflow-field"><span>Images</span><input name="images" type="file" accept="image/*" multiple /></label>
        <div class="ocstoryboard-actions"><button class="secondary" type="button" onClick={() => { window.location.hash = "#/ocstoryboard/tasks"; }}>Cancel</button><button type="submit" disabled={props.busy() === "json"}>{props.busy() === "json" ? "Importing..." : "Import JSON"}</button></div>
      </section>
    </form>
    <form onSubmit={props.onSubmit}>
      <section class="panel ocstoryboard-upload-panel">
        <div class="ocstoryboard-section-title"><h2>Blank StoryBoard</h2><p>上传 SRT 和图片资源池，直接创建新的 Rebuild Session。</p></div>
        <label class="openflow-field"><span>SRT</span><input name="srt" type="file" accept=".srt,text/plain" required /></label>
        <label class="openflow-field"><span>Images</span><input name="images" type="file" accept="image/*" multiple /></label>
        <div class="ocstoryboard-actions"><button class="secondary" type="button" onClick={() => { window.location.hash = "#/ocstoryboard/tasks"; }}>Cancel</button><button type="submit" disabled={props.busy() === "blank"}>{props.busy() === "blank" ? "Creating..." : "Create From SRT"}</button></div>
      </section>
    </form>
  </div>;
}
