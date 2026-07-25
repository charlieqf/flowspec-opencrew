import assert from "node:assert/strict";
import {
  locateStoryboardDialogue,
  routeFromHash,
  safeStoryboardDialogueAssetKey,
} from "../src/modules/koubo/KouboStoryBoard/kouboStoryboardModel.js";

assert.deepEqual(
  routeFromHash("#/koubo-storyboard/tasks/27?dialogue_asset_key=dak_0005"),
  {
    view: "detail",
    taskId: 27,
    dialogueAssetKey: "dak_0005",
    navigationError: "",
  },
);
assert.equal(routeFromHash("#/koubo-storyboard/tasks/27/unknown").view, "list");
const unsafe = routeFromHash("#/koubo-storyboard/tasks/27?dialogue_asset_key=..%2Fsecret");
assert.equal(unsafe.dialogueAssetKey, "");
assert.match(unsafe.navigationError, /不安全/);
assert.equal(safeStoryboardDialogueAssetKey("dak.valid-1"), "dak.valid-1");
assert.equal(safeStoryboardDialogueAssetKey("bad/key"), "");

const plan = {
  shots: [
    { scenes: [{ dialogues: [{ dialogue_id: "dlg_1", dialogue_asset_key: "dak_1" }] }] },
    { scenes: [{ dialogues: [{ dialogue_id: "dlg_2", dialogue_asset_key: "dak_2" }] }] },
  ],
};
assert.deepEqual(locateStoryboardDialogue(plan, "dak_2"), {
  status: "found",
  shotIndex: 1,
  dialogueId: "dlg_2",
});
assert.equal(locateStoryboardDialogue(plan, "dak_missing").status, "missing");
assert.equal(locateStoryboardDialogue({
  shots: [{ scenes: [{ dialogues: [
    { dialogue_id: "dlg_1", dialogue_asset_key: "dak_duplicate" },
    { dialogue_id: "dlg_2", dialogue_asset_key: "dak_duplicate" },
  ] }] }],
}, "dak_duplicate").status, "duplicate");

console.log("koubo storyboard editor return contract: ok");
