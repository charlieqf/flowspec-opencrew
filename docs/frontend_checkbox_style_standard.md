# Checkbox / Radio 样式规范

## 强制原则

全局或容器级文本输入框样式不得直接使用无类型限制的 `input` 选择器。

正确写法：

```css
input:not([type="checkbox"]):not([type="radio"]) {
  /* 文本框、数字框等输入控件样式 */
}
```

Checkbox 和 Radio 必须保持紧凑尺寸，不继承文本框的 `min-width`、`padding` 和固定高度：

```css
input[type="checkbox"],
input[type="radio"] {
  box-sizing: border-box;
  flex: 0 0 auto;
  margin: 0;
  min-height: 0;
  min-width: 0;
  padding: 0;
}
```

## 布局要求

- Checkbox / Radio 与文字之间的间距默认不超过 `6px`。
- 胶囊标签可以使用约 `8px–10px` 的左右视觉内边距；该留白必须来自外层标签，不能来自 Checkbox 自身的宽度或 `min-width`。
- 控件外层标签使用 `width: max-content` 或 `flex: 0 0 auto`，不得无意撑满整行。
- 禁止用左右空白或文本框宽度模拟 Checkbox 的点击区域。
- 新增包含 Checkbox / Radio 的界面时，必须检查是否被上层通用 `input` 规则命中。

## 问题记录

人物口播参考视频选项曾因全局 `input { min-width: 180px; padding: ... }` 与弹窗通用 `input` 规则同时命中，导致 Checkbox 左右出现大面积空白。修复方式是从基础选择器中排除 `checkbox` 和 `radio`，而不是在单个 Checkbox 上反复覆盖宽度。
