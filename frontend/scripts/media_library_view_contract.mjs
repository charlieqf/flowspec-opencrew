import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { normalizeMediaAsset } from "../src/modules/mediaLibrary/mediaLibraryModel.js";
import {
  MEDIA_LIBRARY_CARD_COLUMNS_KEY,
  MEDIA_LIBRARY_VIEW_MODE_KEY,
  addEditableTag,
  mediaLibraryPatchErrorMessage,
  normalizeEditableTags,
  normalizeMediaLibraryCardColumns,
  normalizeMediaLibraryViewMode,
  readMediaLibraryViewPreferences,
  saveMediaLibraryCardColumns,
  saveMediaLibraryViewMode,
} from "../src/modules/mediaLibrary/mediaLibraryViewModel.js";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));

assert.equal(normalizeMediaLibraryViewMode("cards"), "cards");
assert.equal(normalizeMediaLibraryViewMode("grid"), "table");
for (const columns of [2, 3, 4, 5, 6]) {
  assert.equal(normalizeMediaLibraryCardColumns(String(columns)), columns);
}
for (const invalid of [1, 7, "4.5", "bad", null]) {
  assert.equal(normalizeMediaLibraryCardColumns(invalid), 4);
}

const values = new Map([
  [MEDIA_LIBRARY_VIEW_MODE_KEY, "cards"],
  [MEDIA_LIBRARY_CARD_COLUMNS_KEY, "6"],
]);
const storage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
};
assert.deepEqual(readMediaLibraryViewPreferences(storage), { viewMode: "cards", cardColumns: 6 });
assert.equal(saveMediaLibraryViewMode("bad", storage), "table");
assert.equal(saveMediaLibraryCardColumns(2, storage), 2);
assert.equal(values.get(MEDIA_LIBRARY_VIEW_MODE_KEY), "table");
assert.equal(values.get(MEDIA_LIBRARY_CARD_COLUMNS_KEY), "2");
const blockedStorage = {
  getItem: () => { throw new Error("blocked"); },
  setItem: () => { throw new Error("blocked"); },
};
assert.deepEqual(readMediaLibraryViewPreferences(blockedStorage), { viewMode: "table", cardColumns: 4 });
assert.equal(saveMediaLibraryViewMode("cards", blockedStorage), "cards");

assert.deepEqual(normalizeEditableTags([" 访谈 ", "", "x".repeat(40)]), ["访谈", "", "x".repeat(40)]);
assert.deepEqual(addEditableTag(["访谈"], "  横屏 "), ["访谈", "横屏"]);
assert.deepEqual(addEditableTag(["访谈"], "访谈"), ["访谈"]);
assert.equal(addEditableTag([], "x".repeat(33)).length, 0);
assert.equal(addEditableTag(Array.from({ length: 20 }, (_, index) => `t${index}`), "new").length, 20);
assert.equal(
  mediaLibraryPatchErrorMessage(new Error('{"detail":{"code":"media_library_tag_too_long","message":"server"}}')),
  "单个标签最多可包含 32 个字符。",
);
assert.deepEqual(normalizeMediaAsset({ asset_id: "legacy", tags: ["", " old "] }).tags, ["", "old"]);

const [pageSource, tableSource, cardSource, actionsSource, tagEditorSource, tagInputSource, cardCss] = await Promise.all([
  readFile(`${frontendRoot}/src/modules/mediaLibrary/pages/MediaLibraryListPage.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryTable.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryCard.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryAssetActions.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryTagEditor.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/components/MediaLibraryTagInput.jsx`, "utf8"),
  readFile(`${frontendRoot}/src/modules/mediaLibrary/styles/mediaLibraryCards.css`, "utf8"),
]);

assert.match(pageSource, /const \[openMenuId, setOpenMenuId\]/, "one page-owned menu state");
assert.match(pageSource, /closest\?\.\("\.media-library-more-wrap"\)/, "document click handling must not immediately close the delegated menu click");
assert.match(pageSource, /setOpenMenuId\(""\);\s*setViewMode/, "switching view closes the shared menu");
assert.match(pageSource, /MediaLibraryTable[\s\S]*openMenuId=\{openMenuId\(\)\}/);
assert.match(pageSource, /MediaLibraryCardGrid[\s\S]*openMenuId=\{openMenuId\(\)\}/);
assert.match(pageSource, /setItems\(\(current\) => current\.map/, "PATCH response immediately replaces the card/row");
assert.match(pageSource, /await load\(filters\(\)\)/, "facets refresh after saving");
assert.doesNotMatch(pageSource.slice(pageSource.indexOf("function editTags"), pageSource.indexOf("async function lifecycleAction")), /prompt\(/);

for (const source of [tableSource, cardSource]) {
  assert.match(source, /MediaLibraryAssetActions/);
  assert.match(source, /props\.onPreview/);
  assert.match(source, /props\.onDelete/);
}
for (const callback of ["onRename", "onEditTags", "onRestore", "onArchive"]) {
  assert.match(actionsSource, new RegExp(`props\\.${callback}`), callback);
}
assert.match(tagEditorSource, /props\.error/);
assert.match(tagEditorSource, /disabled=\{props\.busy\}/);
assert.match(tagEditorSource, /current\.filter/, "individual chips can be removed");
assert.match(tagInputSource, /props\.suggestions/);
assert.match(tagInputSource, /event\.key === "Enter"/);
assert.match(tagInputSource, /\[,，\]/);

for (const columns of [2, 3, 4, 5, 6]) {
  assert.match(cardCss, new RegExp(`columns-${columns}`));
}
assert.match(cardCss, /@media \(max-width: 1180px\)/);
assert.match(cardCss, /repeat\(3, minmax\(0, 1fr\)\)/);
assert.match(cardCss, /@media \(max-width: 480px\)/);
assert.match(cardCss, /grid-template-columns: minmax\(0, 1fr\)/);

console.log("media library view/tag component contract: ok");
