# 登录页「API Key」Tab 闪现修复

日期：2026-10-27

## 现象

打开登录页，右侧面板先出现三个 Tab（SSO 登录 / 本地账号 / API Key），紧接着 API Key
那一个消失，只剩两个。截图标注位置即该 Tab。

## 根因

Tab 的显隐由 `hideLoginApiKey` 控制，取值来自公开配置 `hide_login_apikey`，只能等
`GET /api/portal/auth/config/public` 异步返回。而该 ref 的初值是 `false`（不隐藏）：

```ts
const hideLoginApiKey = ref(false)   // 首帧按"显示"渲染
```

首帧因此渲染出 API Key Tab，配置回来后置 `true`，`v-for` 的过滤把它删掉——一次
「闪一下然后隐藏」。

同类风险已排查：SSO Tab 依赖 `ssoEnabled && !branding.hide_login_sso`，而 `onMounted`
里是 `await loadBranding()` **之后**才 `fetchPublicConfig()`，branding 先落地，不会出现
「SSO 出现又消失」。API Key 是唯一受影响的 Tab。

## 修复

1. 初值改为隐藏：`const hideLoginApiKey = ref(true)`。
   - `hide_login_apikey = true` 的环境：从第一帧起就不渲染该 Tab，全程无闪现。
   - 允许 API Key 的环境：Tab 在配置返回后出现（向右延长，本地账号位置不动），
     不是闪退，观感可接受。
2. 配置确实没拿到时回退为显示。判定用 `configLoaded` 标志写在 `finally` 里，**不能**
   用 `catch` 兜底：`fetchPublicConfig` 中时区动态导入等后续步骤失败同样会进 `catch`，
   那会把已读到的 `true` 覆盖成 `false`，该藏的 Tab 又冒出来。

## 验证

- `tests/frontend/test_login_apikey_tab_flash_contract.py`（新增 4 项）：初值必须是隐藏、
  Tab 过滤必须使用该标志、缺失配置回退为显示、回退不能挂在 `catch` 上。
- `pytest --confcutdir=tests/frontend -k "login or auth or config_public or boolean_config"`
  → 99 passed / 1 skipped。
- `vue-tsc --noEmit` → 0 错误。

## 未做

- 未执行 `./dev.sh`，未做浏览器手工验收（需重建前端后确认首帧不再出现第三个 Tab）。
- 未改动 SSO Tab 的「延迟出现」特性（品牌与 SSO 开关的加载顺序已保证不会闪退）。
