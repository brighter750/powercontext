# StudyFlow 展示站点（Qoder Sites 发布内容）

本目录是 **Qoder Sites** 可直接发布的静态站点源码，用于公开展示 StudyFlow 参赛作品。

## 目录结构

```text
qoder/site/
├── index.html                  # 单页展示站（唯一入口）
├── README.md                   # 本文件
└── assets/
    ├── css/site.css            # 站点样式（设计令牌与应用端保持一致）
    ├── js/site.js              # 主题切换 / 导航 / 滚动高亮 / 复制 / 灯箱
    └── img/
        ├── favicon.svg
        ├── 01-dashboard.png    # 真实运行截图
        ├── 02-learning-records.png
        ├── 03-weakness-analysis.png
        ├── 04-memory-center.png
        ├── 05-review-plan.png
        └── 06-ai-tutor.png
```

## 站点内容与真实数据的对应关系

站点上的每一个数字都来自真实运行，不是写死的宣传语：

| 站点展示 | 数据来源 |
| --- | --- |
| 208 条 PowerContext 操作 | `qoder/evidence/powercontext-server-operations.json` |
| scope / artifact ID / 修订号 | `qoder/evidence/scenarios/A_write_memory.json` |
| 重启后读到 6 条记录、12 条记忆 | `qoder/evidence/scenarios/E_restart_persistence.json` |
| 两个 Scope 的隔离结果 | `qoder/evidence/scenarios/D_user_isolation.json` |
| 验收样例 A–E 的结果状态 | `qoder/evidence/scenarios/*.json` 的 `result` 字段 |
| 六张界面截图 | `qoder/assets/screenshots/`（真实运行截取） |
| 50 项测试分层 | `qoder/tests/` 下的四个测试文件 |

若后续重新运行验收脚本并产生了新的 artifact ID 或修订号，**请同步更新
`index.html` 中的代码块**，保持站点与证据一致。

## 本地预览

站点是纯静态的，不需要构建，也不需要依赖后端运行：

```powershell
# 任选其一
python -m http.server 5500 --directory qoder/site
# 或
npx serve qoder/site
```

然后访问 <http://127.0.0.1:5500>。

## 在 Qoder Sites 上发布

1. 使用**参赛者本人的 Qoder 个人账号**登录（企业版账号无法设置“任何人都可访问”）。
2. 在 Qoder 中打开本仓库，切到分支 `2026-competition-ecosystem`。
3. 选择 Qoder 的 Sites / 发布功能，站点目录选择 `qoder/site`（入口为 `qoder/site/index.html`）。
4. 发布设置中把访问权限设为**“任何人都可访问”**。
5. 发布成功后，把最终公开网址填回两处：
   - `qoder/README.md` 的「项目链接 → 项目网站」一节（当前为 `待发布`）
   - 本文件末尾的「已发布地址」一节（当前为 `待发布`）
6. 同步更新 `qoder/site/index.html` 页脚或首屏的站点地址提示（如需要）。

## 已发布地址

`待发布`

## 注意事项

- 站点内所有截图均为真实运行截取，请勿替换为设计稿或占位图。
- 站点内没有配置任何 API Key；所有凭据只存在于后端环境变量中。
- 站点未使用 seekDB，也未在文案中把它列为必要组件。
