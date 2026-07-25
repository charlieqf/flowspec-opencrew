# Step 1 AST 迁移工具(过程资产存档)

Step 1(globals→context)使用的三个一次性 AST 变换器,存档供巨文件拆分
(G2/G3)改造复用。均为字节偏移精确改写(ast col_offset 是 UTF-8 字节偏移),
写盘后自带 ast.parse 语法自检。

- `phase_r_rewrite.py <file>`:裸注入名 → `deps.<name>`(Phase R,route 闭包)
- `phase_s_hoist.py <file>`:嵌套 def 上移 module-level + SERVICE_EXPORTS +
  `_sc` 桥(Phase S;tokenize 保护多行字符串的 dedent;默认参数含注入名/
  名字冲突/非 def 语句时中止转人工)
- `phase_f_transform.py [modules...]`:全图过渡签名 `*, sc: Any = None` +
  调用点 `sc=sc` 线程化(Phase F 步骤 1)

注意:三者依赖当时的注入名 snapshot 与门禁模块状态,直接重跑于已完成迁移的
代码库会中止(断言保护)。G2 改造要点见 docs/opencrew_giant_file_split_plan。
